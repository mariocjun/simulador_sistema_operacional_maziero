"""Capacidade dos servidores da seção 2.8 do livro.

O escalonador escolhe uma tarefa servidora; ela atende um pedido da fila global.
Capacidade não é quantum: esgotá-la torna a reserva inelegível até sua reposição.
As funções devolvem (servidor, tipo, detalhe); o motor acrescenta tempo e CPU.
"""


def pedidos_disponiveis(estado):
    """FIFO por ativação; retirar um pedido significa reservá-lo a um servidor."""
    reservados = {t.servidor.cliente for t in estado.tarefas if t.servidor}
    pedidos = [t for t in estado.tarefas
               if t.definicao.tipo in {"aperiodica", "esporadica"}
               and t.chave not in reservados and t.instancia_para_executar() is not None]
    return sorted(pedidos, key=lambda t: (t.instancia_para_executar().ativacao, t.chave))


def devolver_pedido(tarefa):
    """Uma preempção mantém a reserva; conclusão, suspensão ou saldo zero a libera."""
    tarefa.servidor.cliente = None
    tarefa.servidor.instancia_cliente = None


def atualizar(estado):
    """Processar fronteiras após as chegadas, inclusive chegada junto à reposição."""
    registros = []
    concluida = estado.carga_concluida()
    for tarefa in estado.tarefas:
        reserva = tarefa.servidor
        if reserva is None:
            continue
        reserva.concluido = concluida
        cliente = estado.buscar_tarefa(reserva.cliente)
        instancia = cliente.instancia_para_executar() if cliente else None
        if cliente and (instancia is None or instancia.numero != reserva.instancia_cliente or tarefa.suspensa
                        or tarefa.terminou() or
                        (reserva.capacidade == 0 and tarefa.definicao.tipo_servidor != "BS")):
            registros.append((tarefa, "devolucao", f"Pedido de {cliente.definicao.id} liberado"))
            devolver_pedido(tarefa)
    # Todas as devoluções precedem as consultas da fila: a ordem das linhas
    # do arquivo não pode fazer um PS ignorar um pedido liberado neste instante.
    for tarefa in estado.tarefas:
        reserva = tarefa.servidor
        if reserva is None:
            continue
        if tarefa.terminou() or estado.tempo < tarefa.definicao.ingresso:
            continue
        dados = tarefa.definicao
        politica = dados.tipo_servidor
        if not reserva.inicializado:
            reserva.capacidade = dados.duracao
            reserva.proxima_reposicao = dados.ingresso + dados.periodo
            reserva.inicializado = True
            registros.append((tarefa, "reposicao", f"Capacidade inicial: {reserva.capacidade}"))
        elif politica in {"PS", "DS"} and estado.tempo >= reserva.proxima_reposicao:
            # Repor significa substituir o saldo, não somar o que sobrou.
            reserva.capacidade = dados.duracao
            reserva.deadline = estado.tempo + dados.prazo
            reserva.proxima_reposicao = estado.tempo + dados.periodo
            registros.append((tarefa, "reposicao", f"Capacidade reposta: {reserva.capacidade}"))
        if politica == "SS":
            vencidas = [(tempo, valor) for tempo, valor in reserva.reposicoes if tempo <= estado.tempo]
            reserva.reposicoes = [(tempo, valor) for tempo, valor in reserva.reposicoes if tempo > estado.tempo]
            if vencidas:
                quantidade = sum(valor for _, valor in vencidas)
                reserva.capacidade += quantidade
                registros.append((tarefa, "reposicao", f"Restituídos {quantidade} ticks; saldo {reserva.capacidade}"))
        if politica == "PS" and reserva.cliente is None and not pedidos_disponiveis(estado):
            if reserva.capacidade:
                registros.append((tarefa, "esgotamento", "PS sem pedidos: saldo descartado até o próximo período"))
                reserva.capacidade = 0
    return registros


def pode_atender(estado, tarefa):
    """Nunca tornar um servidor pronto para executar CPU sem pedido real."""
    if tarefa.terminou() or tarefa.suspensa or estado.tempo < tarefa.definicao.ingresso:
        return False
    if tarefa.definicao.tipo_servidor != "BS" and tarefa.servidor.capacidade <= 0:
        return False
    return tarefa.servidor.cliente is not None or bool(pedidos_disponiveis(estado))


def reservar_pedido(estado, tarefa):
    """Só a servidora escolhida retira da fila; outras enxergam a fila atualizada."""
    if tarefa.servidor.cliente is not None:
        return []
    pedido = pedidos_disponiveis(estado)[0]
    tarefa.servidor.cliente = pedido.chave
    tarefa.servidor.instancia_cliente = pedido.instancia_para_executar().numero
    return [(tarefa, "atendimento", f"Atendendo {pedido.definicao.id}")]


def deadline(estado, tarefa):
    """EDF ordena a reserva; fora de intervalo ativo SS começa uma nova janela."""
    reserva = tarefa.servidor
    if tarefa.definicao.tipo_servidor == "SS" and (reserva.inicio_intervalo is None
            or estado.tempo >= reserva.inicio_intervalo + tarefa.definicao.periodo):
        return estado.tempo + tarefa.definicao.prazo
    return reserva.deadline


def atualizar_intervalos(estado, candidatas):
    """SS agrupa consumo enquanto sua prioridade ou uma maior ocupa o processador.

Em múltiplas CPUs, a prioridade está ativa se o próprio servidor executa ou
se todas as CPUs executam prioridades pelo menos tão altas. Uma CPU ociosa
ou disponível a prioridade menor encerra o intervalo quando SS não executa.
"""
    por_chave = {c.chave: c for c in candidatas}
    for tarefa in estado.tarefas:
        if tarefa.definicao.tipo_servidor != "SS":
            continue
        reserva = tarefa.servidor
        if tarefa.terminou() or tarefa.suspensa:
            reserva.inicio_intervalo = None
            reserva.consumo_intervalo = 0
            continue
        if estado.tempo < tarefa.definicao.ingresso:
            continue
        prioridade = tarefa.definicao.periodo if estado.algoritmo == "RM" else deadline(estado, tarefa)
        def maior_ou_igual(chave):
            candidata = por_chave.get(chave)
            if candidata is None or candidata.background:
                return False
            valor = candidata.periodo if estado.algoritmo == "RM" else candidata.deadline
            return valor <= prioridade
        ativo = tarefa.chave in estado.cpus or all(maior_ou_igual(chave) for chave in estado.cpus)
        if not ativo:
            reserva.inicio_intervalo = None
            reserva.consumo_intervalo = 0
        elif reserva.inicio_intervalo is None or estado.tempo >= reserva.inicio_intervalo + tarefa.definicao.periodo:
            reserva.inicio_intervalo = estado.tempo
            reserva.consumo_intervalo = 0
            reserva.deadline = estado.tempo + tarefa.definicao.prazo


def consumir(tarefa, tempo):
    """Registrar uma unidade real de serviço, conservando saldo + reposições = C."""
    reserva = tarefa.servidor
    politica = tarefa.definicao.tipo_servidor
    if politica == "BS":
        return False  # Background usa a folga, sem reserva periódica de CPU.
    reserva.capacidade -= 1
    if politica == "SS":
        instante = reserva.inicio_intervalo + tarefa.definicao.periodo
        reserva.consumo_intervalo += 1
        for indice, (quando, valor) in enumerate(reserva.reposicoes):
            if quando == instante:
                reserva.reposicoes[indice] = (quando, valor + 1)
                break
        else:
            reserva.reposicoes.append((instante, 1))
    return reserva.capacidade == 0
