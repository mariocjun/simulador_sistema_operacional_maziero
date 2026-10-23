"""Histórico em memória: cópias do estado, navegação e substituição do futuro.

Cada registro guarda apenas seu quadro e seus eventos. O estado operacional não
contém o histórico; assim um registro não copia todos os registros anteriores.
"""

from copy import deepcopy
from dataclasses import dataclass

from contratos import ErroSimulacao
from modelos import EstadoSimulacao


@dataclass
class _RegistroHistorico:
    estado: EstadoSimulacao
    estado_sorteador: object
    eventos: list
    quadro: dict | None
    avisos: list


def _criar_registro(estado, estado_sorteador, eventos, avisos, quadro=None):
    """Isolar dados mutáveis; getstate() do Random já retorna tuplas imutáveis."""
    return _RegistroHistorico(
        deepcopy(estado), estado_sorteador, deepcopy(eventos),
        deepcopy(quadro), list(avisos))


class HistoricoSimulacao:
    """O cursor escolhe o presente; registros posteriores formam o futuro salvo."""

    def __init__(self, estado, estado_sorteador, eventos, avisos):
        self._registros = [_criar_registro(estado, estado_sorteador, eventos, avisos)]
        self._cursor = 0

    def pode_voltar(self):
        return self._cursor > 0

    def pode_refazer(self):
        return self._cursor + 1 < len(self._registros)

    def restaurar_atual(self):
        """Entregar uma cópia para que o próximo comando não altere o passado."""
        return deepcopy(self._registros[self._cursor])

    def voltar(self):
        if not self.pode_voltar():
            raise ErroSimulacao("Já estamos no instante inicial; não há passo anterior.")
        self._cursor -= 1
        return self.restaurar_atual()

    def refazer(self):
        if not self.pode_refazer():
            raise ErroSimulacao("Não há passo futuro para refazer.")
        self._cursor += 1
        return self.restaurar_atual()

    def registrar(self, estado, estado_sorteador, eventos, avisos, quadro):
        """Acrescentar o resultado de um tick, sem carregar a linha do tempo inteira."""
        registro = _criar_registro(estado, estado_sorteador, eventos, avisos, quadro)
        self._registros[self._cursor + 1:] = [registro]
        self._cursor += 1

    def editar_atual(self, estado, estado_sorteador, eventos, avisos):
        """A edição muda o presente: o futuro calculado antes dela deixa de valer."""
        atual = self._registros[self._cursor]
        registro = _criar_registro(
            estado, estado_sorteador, atual.eventos + eventos, avisos, atual.quadro)
        self._registros[self._cursor:] = [registro]

    def consultar_registros(self):
        """Expor só o passado e o presente, em cópias próprias para a resposta pública."""
        eventos, quadros = [], []
        for registro in self._registros[:self._cursor + 1]:
            eventos.extend(deepcopy(registro.eventos))
            if registro.quadro is not None:
                quadros.append(deepcopy(registro.quadro))
        return eventos, quadros
