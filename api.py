"""API local: valida e coordena comandos, sem conhecer a interface.

O motor executa ticks; HistoricoSimulacao guarda o caminho percorrido.
A API entrega cópias de dados e publica alterações somente após validá-las.
"""

from copy import deepcopy
from dataclasses import asdict, replace
from random import Random
import re

from contratos import Configuracao, DefinicaoTarefa, ErroSimulacao
from escalonadores import ALGORITMOS
from historico import HistoricoSimulacao
from modelos import EstadoSimulacao, Tarefa
import motor


def _validar_inteiro(valor, campo, minimo=0):
    """Regras de tempo são válidas para qualquer consumidor, não apenas a GUI."""
    if type(valor) is not int or valor < minimo:
        raise ErroSimulacao(f"{campo}: informe inteiro maior ou igual a {minimo}.")


def _normalizar_e_validar_definicao(dados):
    """Normalizar texto e validar os campos de uma tarefa sem alterar a entrada."""
    if not isinstance(dados, DefinicaoTarefa):
        raise ErroSimulacao("Use DefinicaoTarefa para informar uma tarefa.")
    if not isinstance(dados.id, str) or not dados.id.strip():
        raise ErroSimulacao("ID não pode ser vazio.")
    if any(ord(c) < 32 or c == ";" for c in dados.id):
        raise ErroSimulacao("ID não pode conter ponto e vírgula ou caracteres de controle.")
    if not isinstance(dados.cor, str) or not re.fullmatch(r"[0-9A-Fa-f]{6}", dados.cor.strip()):
        raise ErroSimulacao("Cor: use seis dígitos RGB hexadecimais, por exemplo 2563EB.")
    for campo in ("ingresso", "duracao", "periodo", "prazo"):
        _validar_inteiro(getattr(dados, campo), campo)
    if not isinstance(dados.eventos, str):
        raise ErroSimulacao("Lista de eventos deve ser um texto.")
    return replace(dados, id=dados.id.strip(), cor=dados.cor.strip().upper())


def _validar_ids_e_cores_unicos(definicoes):
    """IDs são únicos sem distinguir caixa; tarefas periódicas têm cores distintas."""
    ids, cores = set(), set()
    for dados in definicoes:
        if dados.id.casefold() in ids:
            raise ErroSimulacao(f"ID repetido: {dados.id}.")
        ids.add(dados.id.casefold())
        if dados.periodo > 0:
            if dados.cor in cores:
                raise ErroSimulacao(f"Cor repetida: {dados.cor}. Cada tarefa deve ter uma cor distinta.")
            cores.add(dados.cor)


def _validar_mudanca_de_estado(tarefa, nome, cpu, quantidade_cpus):
    """Verificar a transição para um nome já validado, sem modificar a tarefa."""
    if tarefa.terminou():
        raise ErroSimulacao("Tarefa encerrada; retroceda ou reinicie para mudar seu estado.")
    if nome == "nova" and tarefa.instancias:
        raise ErroSimulacao("A tarefa já chegou; retroceda para o estado nova.")
    if nome in {"pronta", "executando"} and not tarefa.instancias_pendentes():
        raise ErroSimulacao(f"Não há instância pronta; próxima ativação: {tarefa.proxima_ativacao}. Use aguardando para retomar.")
    if nome == "aguardando" and tarefa.instancias_pendentes():
        raise ErroSimulacao("Há trabalho pendente; use pronta para retomar.")
    if nome == "executando":
        _validar_inteiro(cpu, "CPU para execução", 1)
        if cpu > quantidade_cpus:
            raise ErroSimulacao("CPU indicada não existe.")


class SimuladorAPI:
    """Uma sessão independente. Os métodos públicos devolvem somente cópias de dados."""

    def __init__(self, configuracao=None, *, semente=None, algoritmos=None):
        self._algoritmos = dict(ALGORITMOS)
        if algoritmos:
            self._algoritmos.update({nome.upper(): funcao for nome, funcao in algoritmos.items()})
        self._semente = semente
        self.configurar(configuracao or Configuracao())

    def configurar(self, configuracao):
        """Validar tudo antes de trocar a sessão: erro de carga não destrói trabalho."""
        if not isinstance(configuracao, Configuracao):
            raise ErroSimulacao("Use Configuracao para iniciar a sessão.")
        if not isinstance(configuracao.algoritmo, str):
            raise ErroSimulacao("Algoritmo deve ser RM ou EDF.")
        algoritmo = configuracao.algoritmo.strip().upper()
        if algoritmo not in self._algoritmos:
            raise ErroSimulacao(f"Algoritmo desconhecido: {algoritmo}.")
        _validar_inteiro(configuracao.cpus, "CPUs", 1)
        _validar_inteiro(configuracao.quantum, "Quantum")
        definicoes = tuple(_normalizar_e_validar_definicao(dados) for dados in configuracao.tarefas)
        _validar_ids_e_cores_unicos(definicoes)
        estado = EstadoSimulacao(algoritmo, configuracao.quantum, [None] * configuracao.cpus)
        avisos = []
        for dados in definicoes:
            if dados.eventos.strip():
                avisos.append(f"{dados.id}: lista de eventos não executada no Projeto A (item 3.3.5).")
            if dados.periodo == 0:
                avisos.append(f"{dados.id}: tarefa aperiódica ignorada (item 4.4).")
                continue
            estado.tarefas.append(Tarefa(estado.proxima_chave, dados, dados.ingresso))
            estado.proxima_chave += 1
        eventos = motor.processar_eventos_do_instante(estado)
        sorteador = Random(self._semente)
        self._inicial = replace(configuracao, algoritmo=algoritmo, tarefas=definicoes)
        self._estado, self._sorteador, self._avisos = estado, sorteador, avisos
        self._historico = HistoricoSimulacao(
            estado, sorteador.getstate(), eventos, avisos)
        return self.consultar_estado()

    def _situacao(self):
        """Distinguir conclusão de bloqueio que exige intervenção humana."""
        tarefas_ativas = [tarefa for tarefa in self._estado.tarefas if not tarefa.terminou()]
        if not tarefas_ativas:
            return "concluida", "Todas as tarefas terminaram; não há trabalho executável."
        for tarefa in tarefas_ativas:
            # Ativações de duração zero podem concluir sem usar CPU, mesmo suspensas.
            ainda_libera_sem_cpu = tarefa.definicao.duracao == 0 and tarefa.pode_liberar_mais_instancias()
            if not tarefa.suspensa or ainda_libera_sem_cpu:
                return "em_andamento", ""
        return "interrompida", "As tarefas restantes estão suspensas. Retome uma tarefa para continuar."

    def consultar_estado(self):
        """Construir uma resposta independente: o front-end não recebe objetos do motor."""
        estado = self._estado
        situacao, motivo = self._situacao()
        resposta = dict(tempo=estado.tempo, algoritmo=estado.algoritmo,
                        quantum=estado.quantum, quantidade_cpus=len(estado.cpus),
                        cpus=list(estado.cpus), situacao=situacao, motivo=motivo,
                        avisos=list(self._avisos), pode_voltar=self._historico.pode_voltar(),
                        pode_refazer=self._historico.pode_refazer(),
                        tarefas=[], quadros=[], eventos=[])
        for tarefa in estado.tarefas:
            dados = asdict(tarefa.definicao)
            cpu = estado.numero_cpu_da_tarefa(tarefa.chave)
            proxima_ativacao = None
            if tarefa.pode_liberar_mais_instancias():
                proxima_ativacao = tarefa.proxima_ativacao
            dados.update(chave=tarefa.chave, estado=estado.nome_estado(tarefa), cpu=cpu,
                         concluidas=tarefa.quantidade_instancias_concluidas(), liberadas=len(tarefa.instancias),
                         pendentes=len(tarefa.instancias_pendentes()),
                         proxima_ativacao=proxima_ativacao,
                         instancias=[asdict(item) for item in tarefa.instancias])
            resposta["tarefas"].append(dados)
        resposta["eventos"], resposta["quadros"] = self._historico.consultar_registros()
        return resposta

    def _avancar(self):
        """Passo interno sem montar o Gantt inteiro a cada tick do modo completo."""
        if self._historico.pode_refazer():
            self._restaurar(self._historico.refazer())
            return
        if self._situacao()[0] == "concluida":
            return
        anterior = self._estado
        estado_sorteador = self._sorteador.getstate()
        self._estado = deepcopy(anterior)
        try:
            quadro, eventos = motor.passo(self._estado, self._algoritmos[self._estado.algoritmo], self._sorteador)
        except Exception:
            self._estado = anterior
            self._sorteador.setstate(estado_sorteador)
            raise
        self._historico.registrar(
            self._estado, self._sorteador.getstate(), eventos, self._avisos, quadro)

    def avancar(self):
        """Mesmo suspensa, uma sessão pode receber um tick manual para inspecionar prazos."""
        self._avancar()
        return self.consultar_estado()

    def _restaurar(self, registro):
        """Sem restaurar o gerador, um empate poderia mudar depois de voltar."""
        self._estado = registro.estado
        self._sorteador.setstate(registro.estado_sorteador)
        self._avisos = registro.avisos

    def voltar(self):
        """Voltar não apaga o futuro; somente uma edição abre uma nova sequência."""
        self._restaurar(self._historico.voltar())
        return self.consultar_estado()

    def executar_completo(self):
        """Rodar até conclusão ou bloqueio real, sem limite artificial de ticks."""
        while self._historico.pode_refazer() or self._situacao()[0] == "em_andamento":
            self._avancar()
        return self.consultar_estado()

    def reiniciar(self):
        """Voltar aos parâmetros da última configuração carregada, descartando edições."""
        return self.configurar(self._inicial)

    def _editar(self, operacao):
        """Transação simples: editar uma cópia e publicar somente depois do sucesso."""
        novo = deepcopy(self._estado)
        eventos, avisos = operacao(novo)
        eventos.extend(motor.processar_eventos_do_instante(novo))
        self._estado = novo
        self._avisos = list(dict.fromkeys(self._avisos + avisos))
        self._historico.editar_atual(
            self._estado, self._sorteador.getstate(), eventos, self._avisos)
        return self.consultar_estado()

    def _tarefa(self, estado, chave):
        """Converter uma chave inválida em erro público compreensível."""
        tarefa = estado.buscar_tarefa(chave) if type(chave) is int else None
        if tarefa is None:
            raise ErroSimulacao(f"Tarefa não encontrada: {chave}.")
        return tarefa

    def alterar_parametros(self, **alteracoes):
        """Novos parâmetros valem para a próxima seleção, nunca para o passado."""
        if set(alteracoes) - {"algoritmo", "cpus", "quantum"}:
            raise ErroSimulacao("Parâmetros aceitos: algoritmo, cpus e quantum.")
        def aplicar(estado):
            if "algoritmo" in alteracoes:
                nome = str(alteracoes["algoritmo"]).strip().upper()
                if nome not in self._algoritmos:
                    raise ErroSimulacao(f"Algoritmo desconhecido: {nome}.")
                estado.algoritmo = nome
            if "quantum" in alteracoes:
                _validar_inteiro(alteracoes["quantum"], "Quantum")
                estado.quantum = alteracoes["quantum"]
            eventos = []
            if "cpus" in alteracoes:
                quantidade = alteracoes["cpus"]
                _validar_inteiro(quantidade, "CPUs", 1)
                for chave in estado.cpus[quantidade:]:
                    if chave is not None:
                        eventos.append(motor.evento(estado, estado.buscar_tarefa(chave), "preempcao", "CPU removida manualmente"))
                estado.cpus = estado.cpus[:quantidade] + [None] * max(0, quantidade - len(estado.cpus))
            eventos.append(dict(tempo=estado.tempo, chave=0, tarefa="Sistema", tipo="manual",
                                detalhe=f"Parâmetros: {alteracoes}", cpu=None))
            return eventos, []
        return self._editar(aplicar)

    def adicionar_tarefa(self, dados):
        """Uma chegada criada durante a simulação não pode ser colocada no passado."""
        dados = _normalizar_e_validar_definicao(dados)
        def aplicar(estado):
            _validar_ids_e_cores_unicos([t.definicao for t in estado.tarefas] + [dados])
            if dados.ingresso < estado.tempo:
                raise ErroSimulacao("Ingresso da nova tarefa não pode ficar no passado.")
            if dados.periodo == 0:
                return [], [f"{dados.id}: tarefa aperiódica ignorada (item 4.4)."]
            tarefa = Tarefa(estado.proxima_chave, dados, dados.ingresso)
            estado.proxima_chave += 1
            estado.tarefas.append(tarefa)
            avisos = [f"{dados.id}: lista de eventos não executada no Projeto A."] if dados.eventos else []
            return [motor.evento(estado, tarefa, "manual", "Tarefa adicionada")], avisos
        return self._editar(aplicar)

    def editar_tarefa(self, chave, **alteracoes):
        """Instâncias liberadas preservam seus tempos e prioridade RM originais."""
        campos = set(DefinicaoTarefa.__dataclass_fields__)
        if set(alteracoes) - campos:
            raise ErroSimulacao("Campo de tarefa desconhecido.")
        def aplicar(estado):
            tarefa = self._tarefa(estado, chave)
            nova = _normalizar_e_validar_definicao(replace(tarefa.definicao, **alteracoes))
            _validar_ids_e_cores_unicos([nova if t.chave == chave else t.definicao for t in estado.tarefas])
            if nova.periodo == 0:
                raise ErroSimulacao("Uma tarefa periódica existente não pode virar aperiódica; use término manual.")
            if nova.ingresso != tarefa.definicao.ingresso:
                if tarefa.instancias or nova.ingresso < estado.tempo:
                    raise ErroSimulacao("Não é possível reescrever um ingresso passado.")
                tarefa.proxima_ativacao = nova.ingresso
            if nova.periodo != tarefa.definicao.periodo and tarefa.instancias:
                tarefa.proxima_ativacao = estado.tempo + nova.periodo
            tarefa.definicao = nova
            avisos = []
            if {"duracao", "prazo", "periodo"} & set(alteracoes):
                avisos.append(f"{nova.id}: alterações temporais valem para ativações futuras; instâncias liberadas preservadas.")
            if nova.eventos:
                avisos.append(f"{nova.id}: lista de eventos não executada no Projeto A.")
            return [motor.evento(estado, tarefa, "manual", f"Edição: {alteracoes}")], avisos
        return self._editar(aplicar)

    def alterar_estado(self, chave, estado, cpu=None):
        """Intervenção visível e reversível. A próxima seleção volta a obedecer RM/EDF."""
        nome = str(estado).strip().casefold()
        if nome not in {"nova", "pronta", "aguardando", "executando", "suspensa", "terminada"}:
            raise ErroSimulacao(f"Estado desconhecido: {estado}.")
        def aplicar(atual):
            tarefa = self._tarefa(atual, chave)
            _validar_mudanca_de_estado(tarefa, nome, cpu, len(atual.cpus))
            eventos = []
            for indice, ocupante in enumerate(atual.cpus):
                if ocupante == chave:
                    atual.cpus[indice] = None
            tarefa.suspensa = nome == "suspensa"
            if nome == "terminada":
                tarefa.encerrada_manualmente = True
            if nome == "executando":
                ocupante = atual.cpus[cpu - 1]
                if ocupante is not None:
                    eventos.append(motor.evento(atual, atual.buscar_tarefa(ocupante), "preempcao", "Intervenção manual", cpu))
                atual.cpus[cpu - 1] = chave
            eventos.append(motor.evento(atual, tarefa, "manual", f"Estado solicitado: {nome}", cpu))
            return eventos, []
        return self._editar(aplicar)
