"""Estado interno simples: um TCB por tarefa, com suas instâncias periódicas."""

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
class Tarefa:
    """TCB. A chave não muda; o identificador apresentado ao aluno pode mudar."""

    chave: int
    definicao: DefinicaoTarefa
    proxima_ativacao: int
    instancias: list[InstanciaTarefa] = field(default_factory=list)
    suspensa: bool = False
    encerrada_manualmente: bool = False

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
        return not self.terminou() and len(self.instancias) < INSTANCIAS_POR_TAREFA

    def concluiu_todas_as_instancias(self):
        """Encerrar manualmente não transforma trabalho pendente em conclusão."""
        return self.quantidade_instancias_concluidas() == INSTANCIAS_POR_TAREFA

    def terminou(self):
        """O recorte do Projeto A acaba após dez conclusões ou intervenção explícita."""
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

    def buscar_tarefa(self, chave):
        """Localizar pela chave estável, não pela posição na lista ou pelo nome."""
        for tarefa in self.tarefas:
            if tarefa.chave == chave:
                return tarefa
        return None

    def numero_cpu_da_tarefa(self, chave):
        """CPUs são apresentadas a partir de 1; None significa que não está em CPU."""
        for numero, ocupante in enumerate(self.cpus, start=1):
            if ocupante is not None and ocupante == chave:
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
        if tarefa.chave in self.cpus:
            return "executando"
        if tarefa.instancias_pendentes():
            return "pronta"
        return "aguardando"
