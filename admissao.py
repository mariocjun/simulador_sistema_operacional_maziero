"""Teste de escalonabilidade da carga finita configurada, antes de aceitá-la.

São conhecidas todas as chegadas: dez ocorrências por periódica, uma por
aperiódica e a lista das esporádicas. Executamos essa carga em uma cópia,
usando a mesma escolha e o mesmo estado do sorteio. Assim o teste inclui
CPUs, reservas, prazos e trabalho que já estava pendente.

Não é uma prova para infinitas chegadas futuras. Novas tarefas e alterações
temporais exigem um novo teste. A análise não grava histórico nem toca na tela.
"""

from copy import deepcopy
from random import Random

from contratos import ErroSimulacao
import motor


def verificar(estado, escolher, sorteador):
    """Retornar o diagnóstico ou recusar antes de publicar a alteração na sessão."""
    copia = deepcopy(estado)
    sorteio = Random()
    sorteio.setstate(sorteador.getstate())
    servidores = [t for t in copia.tarefas if t.servidor and not t.encerrada_manualmente and not t.suspensa]
    for tarefa in copia.tarefas:
        if tarefa.terminou():
            continue
        if tarefa.definicao.periodo <= 0 and tarefa.definicao.duracao > 0 and not servidores:
            raise ErroSimulacao(f"Admissão recusada: {tarefa.definicao.id} precisa de um servidor BS, PS, DS ou SS disponível.")
        for instancia in tarefa.instancias_pendentes():
            if instancia.perdeu_prazo:
                raise ErroSimulacao(f"Admissão recusada: {tarefa.definicao.id} já perdeu o prazo {instancia.deadline}.")
    while not copia.carga_concluida():
        _, eventos = motor.passo(copia, escolher, sorteio)
        for evento in eventos:
            if evento["tipo"] == "prazo":
                raise ErroSimulacao(
                    f"Admissão recusada: {evento['tarefa']} perderia o prazo em t={evento['tempo']}. "
                    f"{evento['detalhe']}. Revise duração, prazo, CPUs ou capacidade dos servidores.")
    return dict(aceita=True, metodo="Simulação antecipada da carga finita",
                tempo_final=copia.tempo,
                detalhe="Ocorrências pendentes e futuras concluem dentro de seus prazos; novas alterações exigem novo teste.")
