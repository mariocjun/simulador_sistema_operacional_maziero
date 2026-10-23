"""Contrato público, independente de widgets, arquivos e implementação do motor.

Cada comando devolve um dicionário independente com:
tempo: int; algoritmo: str; quantum: int; quantidade_cpus: int;
cpus: list[int | None] (chaves das tarefas, posições = números de CPU menos 1);
situacao: 'em_andamento', 'concluida' ou 'interrompida'; motivo: str;
avisos: list[str]; pode_voltar: bool; pode_refazer: bool;
tarefas: list[dict] com chave:int, id/cor/estado/eventos:str,
 ingresso/duracao/periodo/prazo/concluidas/liberadas/pendentes:int,
 proxima_ativacao:int|None, cpu:int|None (número a partir de 1),
 instancias:list[dict] com numero/ativacao/deadline/duracao/periodo/executado:int,
 fim:int|None, perdeu_prazo:bool;
quadros: list[dict] (um por intervalo [tempo,tempo+1)) com tempo:int,
 cpus:list[int|None], tarefas:list[dict] com chave/id/cor/estado/cpu;
eventos: list[dict] com tempo:int, chave:int, tarefa:str, tipo:str, detalhe:str,
 cpu:int|None. Tipos: chegada, ativacao, fim_instancia, termino, prazo,
 sorteio, preempcao, manual.

O front-end desenha esses dados, mas nunca escreve diretamente no motor.
Os dicionários mantêm o contrato curto e podem ser serializados para JSON.
"""

from dataclasses import dataclass


class ErroSimulacao(ValueError):
    """Operação recusada, com mensagem que qualquer front-end pode apresentar."""


@dataclass(frozen=True)
class DefinicaoTarefa:
    """Parâmetros de uma tarefa; uma instância liberada guarda sua própria cópia."""

    id: str = "T1"
    cor: str = "2563EB"
    ingresso: int = 0
    duracao: int = 1
    periodo: int = 5
    prazo: int = 5
    eventos: str = ""


@dataclass(frozen=True)
class Configuracao:
    """Entrada completa. Quantum é preservado no formato, sem rodízio RM/EDF."""

    algoritmo: str = "RM"
    quantum: int = 0
    cpus: int = 1
    tarefas: tuple[DefinicaoTarefa, ...] = ()
