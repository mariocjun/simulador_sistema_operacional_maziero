"""Leitura do formato textual do Projeto A, sem iniciar uma simulação."""

from pathlib import Path
import re

from contratos import Configuracao, DefinicaoTarefa, ErroSimulacao


def _erro(linha: int, campo: str, motivo: str) -> ErroSimulacao:
    return ErroSimulacao(f"Linha {linha}, campo {campo}: {motivo}.")


def _inteiro(texto: str, linha: int, campo: str, minimo: int = 0) -> int:
    if not re.fullmatch(r"[+-]?[0-9]+", texto):
        raise _erro(linha, campo, "informe um número inteiro")
    # A conversão em blocos não herda o teto de dígitos de int(str) do Python.
    # Não altera a configuração global do interpretador nem limita os ticks.
    digitos = texto.lstrip("+-")
    valor = 0
    for inicio in range(0, len(digitos), 1000):
        bloco = digitos[inicio:inicio + 1000]
        valor = valor * 10 ** len(bloco) + int(bloco)
    if texto.startswith("-"):
        valor = -valor
    if valor < minimo:
        raise _erro(linha, campo, f"o valor deve ser maior ou igual a {minimo}")
    return valor


def interpretar(texto: str) -> Configuracao:
    """Interpreta o arquivo inteiro; erros indicam a linha física e o campo.

    IDs e eventos preservam a grafia. Comparações de IDs usam casefold; nomes
    de algoritmo e RGB são normalizados. Eventos são dados reservados ao
    Projeto B. A API informa os avisos e ignora tarefas aperiódicas, por isso
    nenhuma tarefa é descartada aqui.
    """
    linhas = [
        (numero, linha.strip())
        for numero, linha in enumerate(texto.lstrip("\ufeff").splitlines(), 1)
        if linha.strip() and not linha.lstrip().startswith("#")
    ]
    if not linhas:
        raise _erro(1, "cabeçalho", "arquivo vazio; esperado algoritmo;quantum;cpus")

    numero, cabecalho = linhas[0]
    campos = [campo.strip() for campo in cabecalho.split(";")]
    if campos[-1] == "":
        campos.pop()  # Um separador final é opcional, não um quarto campo.
    if len(campos) != 3:
        raise _erro(numero, "cabeçalho", "esperado algoritmo;quantum;cpus (três campos)")
    algoritmo = campos[0].upper()
    if algoritmo not in {"RM", "EDF"}:
        raise _erro(numero, "algoritmo", "somente RM e EDF são aceitos")
    quantum = _inteiro(campos[1], numero, "quantum")
    cpus = _inteiro(campos[2], numero, "cpus", 1)
    if len(linhas) == 1:
        raise _erro(numero + 1, "tarefa", "informe pelo menos uma tarefa")

    tarefas = []
    ids = {}
    cores_periodicas = {}
    for numero, linha in linhas[1:]:
        campos = [campo.strip() for campo in linha.split(";", 6)]
        if len(campos) < 6:
            raise _erro(numero, "tarefa", "esperado id;cor;ingresso;duracao;periodo;prazo;eventos(opcional)")
        identidade, cor = campos[:2]
        if not identidade:
            raise _erro(numero, "id", "a identidade não pode ser vazia")
        if identidade.casefold() in ids:
            raise _erro(numero, "id", f"ID duplicado, já definido na linha {ids[identidade.casefold()]}")
        if not re.fullmatch(r"[0-9A-Fa-f]{6}", cor):
            raise _erro(numero, "cor", "use exatamente seis dígitos hexadecimais, sem prefixo #")
        cor = cor.upper()
        ingresso, duracao, periodo, prazo = (
            _inteiro(valor, numero, nome)
            for nome, valor in zip(("ingresso", "duracao", "periodo", "prazo"), campos[2:6])
        )
        if periodo > 0 and cor in cores_periodicas:
            raise _erro(numero, "cor", f"RGB duplicado entre periódicas, já definido na linha {cores_periodicas[cor]}")
        eventos = campos[6] if len(campos) == 7 else ""
        if eventos.endswith(";"):
            eventos = eventos[:-1].rstrip()
        tarefas.append(DefinicaoTarefa(
            id=identidade, cor=cor, ingresso=ingresso, duracao=duracao,
            periodo=periodo, prazo=prazo, eventos=eventos,
        ))
        ids[identidade.casefold()] = numero
        if periodo > 0:
            cores_periodicas[cor] = numero
    return Configuracao(algoritmo=algoritmo, quantum=quantum, cpus=cpus, tarefas=tuple(tarefas))


def carregar(caminho) -> Configuracao:
    """Lê UTF-8, UTF-16 com BOM ou Windows-1252, sem substituir caracteres."""
    try:
        dados = Path(caminho).read_bytes()
    except (OSError, ValueError) as erro:
        raise ErroSimulacao(f"Não foi possível abrir a configuração {caminho!s}: {erro}") from erro
    try:
        if dados.startswith((b"\xff\xfe\x00\x00", b"\x00\x00\xfe\xff")):
            raise UnicodeError("UTF-32 não é suportado; salve em UTF-8 ou UTF-16 com BOM")
        if dados.startswith((b"\xff\xfe", b"\xfe\xff")):
            texto = dados.decode("utf-16")
        elif dados.startswith(b"\xef\xbb\xbf"):
            texto = dados.decode("utf-8-sig")
        else:
            try:
                texto = dados.decode("utf-8")
            except UnicodeDecodeError:
                texto = dados.decode("cp1252")
        if "\x00" in texto:
            raise UnicodeError("caractere NUL; arquivos UTF-16 precisam de BOM")
    except UnicodeError as erro:
        raise ErroSimulacao(f"Codificação inválida em {caminho!s}: {erro}") from erro
    return interpretar(texto)
