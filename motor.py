"""Um tick: escolher, registrar, executar, avançar e tratar eventos do instante.

As funções alteram apenas o EstadoSimulacao recebido e devolvem registros de eventos.
Não há interface, arquivo, relógio real, thread ou armazenamento global aqui.
"""

from escalonadores import Candidata
from modelos import InstanciaTarefa
from contratos import ErroSimulacao
import servidores


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
                              f"{tarefa.meta_instancias()} instâncias concluídas"))
    return eventos


def processar_eventos_do_instante(estado):
    """Concluir antes de verificar prazos: terminar no deadline não é atraso."""
    eventos = []
    for tarefa in estado.tarefas:
        # Criação da tarefa não é ativação de uma ocorrência: uma esporádica
        # pode ingressar em t=3 e ter sua primeira ocorrência somente em t=8.
        if not tarefa.chegou and estado.tempo >= tarefa.definicao.ingresso:
            tarefa.chegou = True
            eventos.append(evento(estado, tarefa, "chegada"))
        if tarefa.servidor or tarefa.terminou():
            continue
        for item in tarefa.instancias_pendentes():
            if item.consumiu_toda_duracao():
                eventos.extend(_concluir_instancia(estado, tarefa, item))
        if tarefa.concluiu_todas_as_instancias():
            continue
        if tarefa.pode_liberar_mais_instancias() and estado.tempo >= tarefa.proxima_ativacao:
            definicao = tarefa.definicao
            item = InstanciaTarefa(
                numero=len(tarefa.instancias) + 1,
                ativacao=estado.tempo,
                deadline=estado.tempo + definicao.prazo,
                duracao=definicao.duracao,
                periodo=definicao.periodo,
            )
            tarefa.instancias.append(item)
            tarefa.agendar_proxima_ativacao(estado.tempo)
            eventos.append(evento(estado, tarefa, "ativacao", str(item.numero)))
            # Duração zero é permitida: não consome um tick fictício de CPU.
            if item.duracao == 0:
                eventos.extend(_concluir_instancia(estado, tarefa, item))
        for item in tarefa.instancias_pendentes():
            if estado.tempo >= item.deadline and not item.perdeu_prazo:
                item.perdeu_prazo = True
                eventos.append(evento(estado, tarefa, "prazo",
                                      f"Instância {item.numero}; deadline {item.deadline}"))
    eventos.extend(_eventos_servidor(estado, servidores.atualizar(estado)))
    # Uma CPU não continua apontando para trabalho que acabou na fronteira.
    for indice, chave in enumerate(estado.cpus):
        if chave is not None:
            tarefa = estado.buscar_tarefa(chave)
            if (tarefa.servidor and not servidores.pode_atender(estado, tarefa)) or (
                    not tarefa.servidor and tarefa.instancia_para_executar() is None):
                estado.cpus[indice] = None
    return eventos


def _eventos_servidor(estado, registros):
    """A política fornece o fato; o motor acrescenta seu instante na simulação."""
    return [evento(estado, tarefa, tipo, detalhe, estado.numero_cpu_da_tarefa(tarefa.chave))
            for tarefa, tipo, detalhe in registros]


def _reunir_candidatas(estado):
    """Só a instância pendente mais antiga de cada tarefa disputa uma CPU."""
    candidatas = []
    for tarefa in estado.tarefas:
        if tarefa.servidor:
            if servidores.pode_atender(estado, tarefa):
                dados = tarefa.definicao
                candidatas.append(Candidata(tarefa.chave, dados.periodo, servidores.deadline(estado, tarefa),
                                           tarefa.chave in estado.cpus, dados.prazo, dados.ingresso,
                                           dados.duracao, dados.tipo_servidor == "BS"))
            continue
        if tarefa.definicao.periodo <= 0:
            continue  # Pedidos não disputam CPU diretamente: usam os servidores.
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
                                      cor=tarefa.definicao.cor, estado=estado.nome_estado(tarefa), cpu=cpu,
                                      **dados_de_atendimento(estado, tarefa)))
    return quadro


def dados_de_atendimento(estado, tarefa):
    """Identificar no contrato quem fornece CPU e qual pedido consome essa CPU."""
    servidor = estado.servidor_da_tarefa(tarefa.chave)
    cliente = estado.buscar_tarefa(tarefa.servidor.cliente) if tarefa.servidor else None
    return dict(tipo=tarefa.definicao.tipo, tipo_servidor=tarefa.definicao.tipo_servidor,
                servidor=servidor.definicao.id if servidor else None,
                cliente=cliente.definicao.id if cliente else None)


def passo(estado, escolher, sorteador):
    """Orquestrar um tick; os auxiliares explicam cada etapa sem escolher a política."""
    cpus_anteriores = list(estado.cpus)
    candidatas_iniciais = _reunir_candidatas(estado)
    escolhidas, sorteadas, registros = [], set(), []
    # Atualizar a fila depois de cada escolha impede que duas reservas retirem
    # o mesmo pedido ou que uma CPU seja atribuída a um servidor sem serviço.
    while len(escolhidas) < len(estado.cpus):
        candidatas = [c for c in _reunir_candidatas(estado) if c.chave not in escolhidas]
        if not candidatas:
            break
        novas, por_sorteio = _selecionar_tarefas(candidatas, 1, escolher, sorteador)
        escolhidas.extend(novas)
        sorteadas.update(por_sorteio)
        selecionada = estado.buscar_tarefa(novas[0])
        if selecionada.servidor:
            registros.extend(servidores.reservar_pedido(estado, selecionada))
    estado.cpus = _distribuir_cpus(cpus_anteriores, escolhidas)
    eventos = _registrar_trocas(estado, cpus_anteriores, escolhidas, sorteadas)
    eventos.extend(_eventos_servidor(estado, registros))
    servidores.atualizar_intervalos(estado, candidatas_iniciais)
    quadro = _registrar_quadro(estado)

    # O quadro descreve o intervalo que será executado, não o instante após ele.
    esgotados = []
    for tarefa in estado.tarefas:
        if tarefa.chave in estado.cpus:
            executora = estado.buscar_tarefa(tarefa.servidor.cliente) if tarefa.servidor else tarefa
            executora.instancia_para_executar().executar_um_tick()
            if tarefa.servidor and servidores.consumir(tarefa, estado.tempo):
                esgotados.append(tarefa)
    estado.tempo += 1
    eventos.extend(evento(estado, t, "esgotamento", "Capacidade consumida") for t in esgotados)
    eventos.extend(processar_eventos_do_instante(estado))
    return quadro, eventos
