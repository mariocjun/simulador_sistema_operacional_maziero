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
 sorteio, preempcao, manual, reposicao, esgotamento, atendimento, devolucao.

Projeto B acrescenta admissao:dict ao estado. Cada tarefa/miniatura inclui
tipo, tipo_servidor, servidor (ID que atende) e cliente (ID atendido).
O TCB público também informa capacidade, reposicoes, meta_instancias e
encerrada_manualmente. Em serviço, CPU aponta para a chave do servidor;
suas duas linhas (servidor e cliente) representam a mesma unidade de CPU.

O front-end desenha esses dados, mas nunca escreve diretamente no motor.
Os dicionários mantêm o contrato curto e podem ser serializados para JSON.
"""

from dataclasses import dataclass
import re


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

    @property
    def tipo_servidor(self):
        """O prefixo reservado do ID identifica a política, sem distinguir caixa."""
        encontrado = re.fullmatch(r"(BS|PS|DS|SS)[0-9]+", self.id.upper())
        return encontrado.group(1) if encontrado else ""

    @property
    def tipo(self):
        """O sinal do período tem a semântica definida no item B.1 do enunciado."""
        if self.tipo_servidor:
            return "servidor"
        if self.periodo > 0:
            return "periodica"
        return "aperiodica" if self.periodo == 0 else "esporadica"

    def deslocamentos(self):
        """Instantes relativos das esporádicas; não inventar ativações periódicas."""
        if self.periodo >= 0:
            return ()
        partes = [parte.strip() for parte in self.eventos.split(",")]
        if not partes or any(not re.fullmatch(r"[0-9]+", parte) for parte in partes):
            raise ErroSimulacao(f"{self.id}: lista de eventos exige ocorrências inteiras, como 0,10,25.")
        valores = tuple(int(parte) for parte in partes)
        if any(b - a < abs(self.periodo) for a, b in zip(valores, valores[1:])):
            raise ErroSimulacao(f"{self.id}: intervalo entre ocorrências deve ser pelo menos {abs(self.periodo)} ticks.")
        return valores


@dataclass(frozen=True)
class Configuracao:
    """Entrada completa. Quantum é preservado no formato, sem rodízio RM/EDF."""

    algoritmo: str = "RM"
    quantum: int = 0
    cpus: int = 1
    tarefas: tuple[DefinicaoTarefa, ...] = ()
