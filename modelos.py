"""Estado interno: um TCB por tarefa; cada ocorrência conserva seus próprios tempos."""

from dataclasses import dataclass, field
from contratos import DefinicaoTarefa


INSTANCIAS_POR_TAREFA = 10


@dataclass
class InstanciaTarefa:
    """Uma ativação mantém seus próprios tempos, mesmo se a tarefa for editada."""

    numero: int
    ativacao: int
    deadline: int
    duracao: int
    periodo: int
    executado: int = 0
    fim: int | None = None
    perdeu_prazo: bool = False

    def tempo_restante(self):
        """Tempo de CPU que esta ativação ainda precisa consumir."""
        return max(0, self.duracao - self.executado)

    def consumiu_toda_duracao(self):
        """Consumir a duração não registra o instante de conclusão: o motor o faz."""
        return self.tempo_restante() == 0

    def executar_um_tick(self):
        """A instância controla seu consumo; duração zero não exige CPU."""
        if not self.consumiu_toda_duracao():
            self.executado += 1


@dataclass
class EstadoServidor:
    """Reserva e pedido atual também pertencem ao estado restaurado pelo histórico."""

    capacidade: int = 0
    proxima_reposicao: int = 0
    deadline: int = 0
    cliente: int | None = None
    instancia_cliente: int | None = None
    inicializado: bool = False
    concluido: bool = False
    reposicoes: list[tuple[int, int]] = field(default_factory=list)
    inicio_intervalo: int | None = None
    consumo_intervalo: int = 0


@dataclass
class Tarefa:
    """TCB. A chave não muda; o identificador apresentado ao aluno pode mudar."""

    chave: int
    definicao: DefinicaoTarefa
    proxima_ativacao: int
    instancias: list[InstanciaTarefa] = field(default_factory=list)
    suspensa: bool = False
    encerrada_manualmente: bool = False
    chegou: bool = False
    servidor: EstadoServidor | None = None

    def __post_init__(self):
        if self.definicao.tipo_servidor:
            self.servidor = EstadoServidor(proxima_reposicao=self.definicao.ingresso,
                                           deadline=self.definicao.ingresso + self.definicao.prazo)
        elif self.definicao.periodo < 0:
            self.proxima_ativacao = self.definicao.ingresso + self.definicao.deslocamentos()[0]

    def meta_instancias(self):
        """Dez periódicas, uma aperiódica ou todas as ocorrências explicitadas."""
        if self.servidor:
            return None
        if self.definicao.periodo < 0:
            return len(self.definicao.deslocamentos())
        return INSTANCIAS_POR_TAREFA if self.definicao.periodo > 0 else 1

    def agendar_proxima_ativacao(self, tempo):
        """A lista esporádica é relativa ao ingresso, não à última conclusão."""
        if self.definicao.periodo < 0:
            instantes = self.definicao.deslocamentos()
            if len(self.instancias) < len(instantes):
                self.proxima_ativacao = self.definicao.ingresso + instantes[len(self.instancias)]
        elif self.definicao.periodo > 0:
            self.proxima_ativacao = tempo + self.definicao.periodo

    def instancias_pendentes(self):
        """Instâncias da mesma tarefa executam sequencialmente, em ordem de liberação."""
        return [item for item in self.instancias if item.fim is None]

    def instancia_para_executar(self):
        """Suspensão impede execução, mas não apaga pendências nem seus prazos."""
        if self.suspensa or self.terminou():
            return None
        for instancia in self.instancias:
            if instancia.fim is None:
                return instancia
        return None

    def quantidade_instancias_concluidas(self):
        """Contar conclusões, não ativações ou perdas de prazo."""
        return sum(item.fim is not None for item in self.instancias)

    def pode_liberar_mais_instancias(self):
        """Limitar ativações sem confundir trabalho liberado com concluído."""
        return not self.servidor and not self.terminou() and len(self.instancias) < self.meta_instancias()

    def concluiu_todas_as_instancias(self):
        """Encerrar manualmente não transforma trabalho pendente em conclusão."""
        return not self.servidor and self.quantidade_instancias_concluidas() == self.meta_instancias()

    def terminou(self):
        """Servidores existem enquanto a carga precisa deles; não executam trabalho fictício."""
        if self.servidor:
            return self.encerrada_manualmente or self.servidor.concluido
        return self.encerrada_manualmente or self.concluiu_todas_as_instancias()


@dataclass
class EstadoSimulacao:
    """Tudo que deve ser restaurado ao voltar, sem histórico dentro do histórico."""

    algoritmo: str
    quantum: int
    cpus: list[int | None]
    tarefas: list[Tarefa] = field(default_factory=list)
    tempo: int = 0
    proxima_chave: int = 1
    admissao: dict = field(default_factory=dict)

    def carga_concluida(self):
        """Servidores são recursos de atendimento, não pedidos a concluir."""
        return all(t.terminou() for t in self.tarefas if not t.servidor)

    def servidor_da_tarefa(self, chave):
        """Uma reserva de pedido é exclusiva, mesmo durante uma preempção."""
        return next((t for t in self.tarefas if t.servidor and t.servidor.cliente == chave), None)

    def buscar_tarefa(self, chave):
        """Localizar pela chave estável, não pela posição na lista ou pelo nome."""
        for tarefa in self.tarefas:
            if tarefa.chave == chave:
                return tarefa
        return None

    def numero_cpu_da_tarefa(self, chave):
        """CPUs são apresentadas a partir de 1; None significa que não está em CPU."""
        for numero, ocupante in enumerate(self.cpus, start=1):
            tarefa = self.buscar_tarefa(ocupante)
            if ocupante is not None and (ocupante == chave or
                    (tarefa and tarefa.servidor and tarefa.servidor.cliente == chave)):
                return numero
        return None

    def nome_estado(self, tarefa):
        """Derivar estado visível dos fatos, sem armazenar flags contraditórias."""
        if tarefa.terminou():
            return "terminada"
        if self.tempo < tarefa.definicao.ingresso:
            return "nova"
        if tarefa.suspensa:
            return "suspensa"
        if self.numero_cpu_da_tarefa(tarefa.chave) is not None:
            return "executando"
        if tarefa.servidor:
            return "pronta" if tarefa.servidor.cliente is not None and tarefa.servidor.capacidade > 0 else "aguardando"
        if tarefa.instancias_pendentes():
            return "pronta"
        return "aguardando"
