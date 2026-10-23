"""Um tick: escolher, registrar, executar, avançar e tratar eventos do instante.

As funções alteram apenas o EstadoSimulacao recebido e devolvem registros de eventos.
Não há interface, arquivo, relógio real, thread ou armazenamento global aqui.
"""

from escalonadores import Candidata
from modelos import INSTANCIAS_POR_TAREFA, InstanciaTarefa
from contratos import ErroSimulacao


def evento(estado, tarefa, tipo, detalhe="", cpu=None):
    """Evento sem coordenadas: cada apresentação decide como desenhá-lo."""
    return dict(tempo=estado.tempo, chave=tarefa.chave, tarefa=tarefa.definicao.id,
                tipo=tipo, detalhe=detalhe, cpu=cpu)


def _concluir_instancia(estado, tarefa, instancia):
    """Registrar a conclusão e, se for a última, o término da tarefa."""
    instancia.fim = estado.tempo
    eventos = [evento(estado, tarefa, "fim_instancia", str(instancia.numero))]
    if tarefa.concluiu_todas_as_instancias():
        eventos.append(evento(estado, tarefa, "termino",
                              f"{INSTANCIAS_POR_TAREFA} instâncias concluídas"))
    return eventos


def processar_eventos_do_instante(estado):
    """Concluir antes de verificar prazos: terminar no deadline não é atraso."""
    eventos = []
    for tarefa in estado.tarefas:
        if tarefa.terminou():
            continue
        for item in tarefa.instancias_pendentes():
            if item.consumiu_toda_duracao():
                eventos.extend(_concluir_instancia(estado, tarefa, item))
        if tarefa.concluiu_todas_as_instancias():
            continue
        if tarefa.pode_liberar_mais_instancias() and estado.tempo >= tarefa.proxima_ativacao:
            if not tarefa.instancias:
                eventos.append(evento(estado, tarefa, "chegada"))
            definicao = tarefa.definicao
            item = InstanciaTarefa(
                numero=len(tarefa.instancias) + 1,
                ativacao=estado.tempo,
                deadline=estado.tempo + definicao.prazo,
                duracao=definicao.duracao,
                periodo=definicao.periodo,
            )
            tarefa.instancias.append(item)
            tarefa.proxima_ativacao = estado.tempo + definicao.periodo
            eventos.append(evento(estado, tarefa, "ativacao", str(item.numero)))
            # Duração zero é permitida: não consome um tick fictício de CPU.
            if item.duracao == 0:
                eventos.extend(_concluir_instancia(estado, tarefa, item))
        for item in tarefa.instancias_pendentes():
            if estado.tempo >= item.deadline and not item.perdeu_prazo:
                item.perdeu_prazo = True
                eventos.append(evento(estado, tarefa, "prazo",
                                      f"Instância {item.numero}; deadline {item.deadline}"))
    # Uma CPU não continua apontando para trabalho que acabou na fronteira.
    for indice, chave in enumerate(estado.cpus):
        if chave is not None:
            tarefa = estado.buscar_tarefa(chave)
            if tarefa.instancia_para_executar() is None:
                estado.cpus[indice] = None
    return eventos


def _reunir_candidatas(estado):
    """Só a instância pendente mais antiga de cada tarefa disputa uma CPU."""
    candidatas = []
    for tarefa in estado.tarefas:
        instancia = tarefa.instancia_para_executar()
        if instancia is not None:
            candidatas.append(Candidata(
                chave=tarefa.chave,
                periodo=instancia.periodo,
                deadline=instancia.deadline,
                executava=tarefa.chave in estado.cpus,
                prazo=instancia.deadline - instancia.ativacao,
                ingresso=tarefa.definicao.ingresso,
                duracao=instancia.duracao,
            ))
    return candidatas


def _selecionar_tarefas(candidatas, quantidade_cpus, escolher, sorteador):
    """O algoritmo decide a prioridade; remover a escolhida evita execução paralela."""
    escolhidas, sorteadas = [], set()
    while candidatas and len(escolhidas) < quantidade_cpus:
        chave, houve_sorteio = escolher(tuple(candidatas), sorteador.choice)
        if chave not in [c.chave for c in candidatas]:
            raise ErroSimulacao("Escalonador devolveu tarefa não elegível.")
        escolhidas.append(chave)
        if houve_sorteio:
            sorteadas.add(chave)
        candidatas = [c for c in candidatas if c.chave != chave]
    return escolhidas, sorteadas


def _distribuir_cpus(cpus_anteriores, escolhidas):
    """Quem continua selecionado permanece na mesma CPU; os demais ocupam vagas."""
    cpus = [chave if chave in escolhidas else None for chave in cpus_anteriores]
    for chave in escolhidas:
        if chave not in cpus:
            cpus[cpus.index(None)] = chave
    return cpus


def _registrar_trocas(estado, cpus_anteriores, escolhidas, sorteadas):
    """Preempção e início por sorteio são eventos, não estados permanentes da tarefa."""
    eventos = []
    for chave in cpus_anteriores:
        if chave is not None and chave not in escolhidas:
            eventos.append(evento(estado, estado.buscar_tarefa(chave), "preempcao"))
    for chave in sorteadas:
        # Escolher novamente uma tarefa que já executa não é início/retomada.
        if chave not in cpus_anteriores:
            eventos.append(evento(estado, estado.buscar_tarefa(chave), "sorteio",
                                  "Desempate pelo item 4.3", estado.numero_cpu_da_tarefa(chave)))
    return eventos


def _registrar_quadro(estado):
    """Retratar o intervalo [t, t+1) antes de consumir seu tempo de CPU."""
    quadro = dict(tempo=estado.tempo, cpus=list(estado.cpus), tarefas=[])
    for tarefa in estado.tarefas:
        cpu = estado.numero_cpu_da_tarefa(tarefa.chave)
        quadro["tarefas"].append(dict(chave=tarefa.chave, id=tarefa.definicao.id,
                                      cor=tarefa.definicao.cor, estado=estado.nome_estado(tarefa), cpu=cpu))
    return quadro


def passo(estado, escolher, sorteador):
    """Orquestrar um tick; os auxiliares explicam cada etapa sem escolher a política."""
    cpus_anteriores = list(estado.cpus)
    candidatas = _reunir_candidatas(estado)
    escolhidas, sorteadas = _selecionar_tarefas(candidatas, len(estado.cpus), escolher, sorteador)
    estado.cpus = _distribuir_cpus(cpus_anteriores, escolhidas)
    eventos = _registrar_trocas(estado, cpus_anteriores, escolhidas, sorteadas)
    quadro = _registrar_quadro(estado)

    # O quadro descreve o intervalo que será executado, não o instante após ele.
    for tarefa in estado.tarefas:
        if tarefa.chave in estado.cpus:
            tarefa.instancia_para_executar().executar_um_tick()
    estado.tempo += 1
    eventos.extend(processar_eventos_do_instante(estado))
    return quadro, eventos
