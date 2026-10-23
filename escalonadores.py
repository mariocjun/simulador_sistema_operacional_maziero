"""Funções substituíveis de escolha. O motor não contém um if para RM ou EDF."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Candidata:
    """Dados de seleção, sem conceder ao escalonador acesso mutável ao TCB."""

    chave: int
    periodo: int
    deadline: int
    executava: bool
    prazo: int
    ingresso: int
    duracao: int
    background: bool = False


def _escolher(candidatas, sortear, prioridade):
    """Aplicar exatamente a ordem de desempates do item 4.3, após a prioridade."""
    def criterio(candidata):
        return (candidata.background, prioridade(candidata), not candidata.executava,
                candidata.prazo, candidata.ingresso, candidata.duracao)

    melhor = min(criterio(candidata) for candidata in candidatas)
    empatadas = [c for c in candidatas if criterio(c) == melhor]
    if len(empatadas) == 1:
        return empatadas[0].chave, False
    return sortear(empatadas).chave, True


def rm(candidatas, sortear):
    """Rate Monotonic: menor período primeiro (capítulo 2, seção 2.4.1)."""
    return _escolher(candidatas, sortear, lambda candidata: candidata.periodo)


def edf(candidatas, sortear):
    """EDF: menor prazo absoluto pendente primeiro (capítulo 2, seção 2.4.2)."""
    return _escolher(candidatas, sortear, lambda candidata: candidata.deadline)


ALGORITMOS = {"RM": rm, "EDF": edf}
