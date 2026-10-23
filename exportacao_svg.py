"""SVG completo a partir exclusivamente do dicionário público da simulação.

Não consulta API, modelos, GUI ou configuração global. Cada quadro representa
um intervalo [t, t+1); eventos ficam exatamente na fronteira informada.
"""

from collections import Counter
from decimal import Decimal
from pathlib import Path
import re
from xml.etree import ElementTree as ET

from contratos import ErroSimulacao


_PASSO = 28
_EVENTOS = {
    "chegada": ("Chegada", "M 0,-6 L 6,5 L -6,5 Z", "#0369A1"),
    "ativacao": ("Ativação", "M 0,-6 L 6,0 L 0,6 L -6,0 Z", "#047857"),
    "fim_instancia": ("Conclusão de instância", "M -6,0 L -1,5 L 6,-5", "#15803D"),
    "termino": ("Término da tarefa", "M -5,-5 H 5 V 5 H -5 Z", "#334155"),
    "preempcao": ("Preempção", "M -6,-5 L 0,0 L -6,5 M 0,0 H 6", "#B45309"),
    "sorteio": ("Sorteio", "M 0,-6 V 6 M -6,0 H 6 M -4,-4 L 4,4 M -4,4 L 4,-4", "#7E22CE"),
    "prazo": ("Perda de prazo", "M -5,-5 L 5,5 M -5,5 L 5,-5", "#DC2626"),
    "manual": ("Intervenção manual", "M 0,-6 V 3 M -1,6 H 1", "#A21CAF"),
    "reposicao": ("Reposição de capacidade", "M 0,-6 V 6 M -6,0 H 6", "#0369A1"),
    "esgotamento": ("Capacidade esgotada", "M -4,-4 H 4 V 4 H -4 Z M -6,6 L 6,-6", "#B45309"),
    "atendimento": ("Atendimento de cliente", "M -6,0 H 6 M 1,-5 L 6,0 L 1,5", "#047857"),
    "devolucao": ("Devolução de cliente", "M 6,-5 V 3 H -6 M -1,-2 L -6,3 L -1,8", "#7E22CE"),
}
_TIPOS = {"periodica": "Periódica", "aperiodica": "Aperiódica",
          "esporadica": "Esporádica", "servidor": "Servidor"}


def _numero(valor: int) -> str:
    """Coordenadas inteiras, inclusive além do limite decimal padrão do Python."""
    try:
        return str(valor)
    except ValueError:
        negativo = valor < 0
        valor = abs(valor)
        blocos = []
        while valor:
            valor, resto = divmod(valor, 10 ** 1000)
            blocos.append(str(resto))
        return ("-" if negativo else "") + blocos[-1] + "".join(
            bloco.zfill(1000) for bloco in reversed(blocos[:-1])
        )


def _elemento(pai, nome, texto=None, **atributos):
    elemento = ET.SubElement(pai, nome, {
        chave.replace("_", "-"): _numero(valor) if isinstance(valor, int) else str(valor)
        for chave, valor in atributos.items()
    })
    if texto is not None:
        elemento.text = str(texto)
    return elemento


def _ordem_id(identidade):
    # Tuplas com etiquetas permitem misturar IDs numéricos e textuais.
    identidade = str(identidade).casefold()
    if re.fullmatch(r"[+-]?\d+", identidade):
        return ((0, Decimal(identidade)),)
    return tuple(
        (0, Decimal(parte)) if parte.isdecimal() else (1, parte)
        for parte in re.split(r"(\d+)", identidade) if parte
    )


def _juntar(faixas, chave, inicio, assinatura):
    """Compacta somente intervalos contíguos com os mesmos dados visuais."""
    segmentos = faixas.setdefault(chave, [])
    if segmentos and segmentos[-1][1] == inicio and segmentos[-1][2] == assinatura:
        segmentos[-1][1] = inicio + 1
    else:
        segmentos.append([inicio, inicio + 1, assinatura])


def _estilo(estado, cor, tipo="periodica"):
    if estado == "executando":
        if tipo == "servidor":
            return {"fill": "#" + cor, "stroke": "#0F172A", "stroke_width": 2}
        if tipo in {"aperiodica", "esporadica"}:
            return {"fill": "url(#cliente-" + cor + ")", "stroke": "#" + cor,
                    "stroke_width": 2, "stroke_dasharray": "4 2"}
        return {"fill": "#" + cor, "stroke": "#334155"}
    if estado == "pronta":
        return {"fill": "none", "stroke": "#475569"}
    if estado == "suspensa":
        return {"fill": "url(#suspensa)", "stroke": "#000000"}
    if estado in {"terminada", "encerrada"}:
        return {"fill": "#CBD5E1", "stroke": "#64748B", "stroke_dasharray": "2 3"}
    if estado == "nova":
        return {"fill": "#F8FAFC", "stroke": "#94A3B8", "stroke_dasharray": "5 3"}
    return {"fill": "#E2E8F0", "stroke": "#94A3B8"}


def _marcador(pai, tipo, x, y, **atributos):
    return _elemento(pai, "use", href="#evento-" + tipo, x=x, y=y, **atributos)


def _vinculo(tipo, tipo_servidor, servidor, cliente):
    """Descreve os papéis do quadro histórico, sem consultar o TCB atual."""
    partes = [_TIPOS.get(tipo, tipo)]
    if tipo_servidor:
        partes.append(tipo_servidor)
    if servidor is not None:
        partes.append("servidor: " + str(servidor))
    if cliente is not None:
        partes.append("cliente: " + str(cliente))
    return " · ".join(partes)


def _borda_servidor(pai, x, y, largura, altura):
    # Borda dupla preserva a cor do servidor, mesmo em impressão sem cores.
    _elemento(pai, "rect", x=x + 3, y=y + 3, width=largura - 6, height=altura - 6,
              fill="none", stroke="#FFFFFF", pointer_events="none")


def gerar_svg(estado: dict) -> str:
    """Desenha todo o histórico visível, sem limite de ticks, linhas ou viewport.

    Os rótulos usam IDs atuais, mas as faixas usam cor/estado/CPU históricos.
    A chave pública liga os registros após renomeações. O eixo pode ter marcas
    espaçadas; nenhum intervalo ou evento é amostrado ou descartado.
    """
    quadros = estado.get("quadros", [])
    eventos = estado.get("eventos", [])
    tarefas = {}
    for quadro in quadros:
        for tarefa in quadro["tarefas"]:
            tarefas[tarefa["chave"]] = tarefa
    for evento in eventos:
        tarefas.setdefault(evento["chave"], {"chave": evento["chave"], "id": evento["tarefa"]})
    for tarefa in estado.get("tarefas", []):
        tarefas[tarefa["chave"]] = tarefa
    chaves = sorted(tarefas, key=lambda chave: _ordem_id(tarefas[chave]["id"]), reverse=True)
    quantidade_cpus = max(
        [estado.get("quantidade_cpus", 0), len(estado.get("cpus", []))]
        + [len(quadro["cpus"]) for quadro in quadros]
    )
    fim = max([0, estado.get("tempo", 0)]
              + [quadro["tempo"] + 1 for quadro in quadros]
              + [evento["tempo"] for evento in eventos])
    esquerda = max(200, max((len(str(t["id"])) * 8 + 36 for t in tarefas.values()), default=0))
    direita = esquerda + max(1, fim) * _PASSO
    largura = max(1200, direita + 80)
    simultaneos = Counter((evento["chave"], evento["tempo"]) for evento in eventos)
    alturas = {chave: 52 for chave in chaves}
    for (chave, _), quantidade in simultaneos.items():
        alturas[chave] = max(alturas[chave], quantidade * 14 + 40)
    linhas = {}
    y = 164  # Reservar uma faixa exclusiva para a contagem de perdas no cabeçalho.
    for chave in chaves:
        linhas[chave] = y
        y += alturas[chave]
    y += 30
    linhas_cpu = {}
    for cpu in range(quantidade_cpus, 0, -1):
        linhas_cpu[cpu] = y
        y += 44
    eixo = y + 20
    legenda = eixo + 62
    base_estados = legenda + 32 + ((len(_EVENTOS) + 3) // 4) * 25
    altura = base_estados + 125
    raiz = ET.Element("svg", {
        "xmlns": "http://www.w3.org/2000/svg", "version": "1.1",
        "width": _numero(largura), "height": _numero(altura),
        "viewBox": f"0 0 {_numero(largura)} {_numero(altura)}",
        "role": "img", "aria-labelledby": "titulo descricao",
        "font-family": "sans-serif", "font-size": "12",
    })
    encerrada = estado.get("situacao") == "concluida"
    manual = encerrada and any(t.get("encerrada_manualmente", False)
                               for t in estado.get("tarefas", []))
    titulo = "Projeto B — " + ("Simulação encerrada manualmente" if manual else (
        "Simulação concluída" if encerrada else "Simulação parcial"))
    # Eventos pertencem ao histórico recebido, inclusive em exportações parciais.
    perdas = sum(evento["tipo"] == "prazo" for evento in eventos)
    prazos = f"{_numero(perdas)} {'perda' if perdas == 1 else 'perdas'} de prazo"
    titulo += " — " + prazos
    _elemento(raiz, "title", titulo, id="titulo")
    _elemento(raiz, "desc", "Histórico completo do estado recebido; tarefas em ID decrescente e faixas de todas as CPUs.", id="descricao")
    _elemento(raiz, "rect", width=largura, height=altura, fill="#FFFFFF")
    _elemento(raiz, "text", titulo, x=20, y=30, font_size=20, font_weight="bold")
    _elemento(raiz, "text", f"{estado.get('algoritmo', '')} · tempo: {_numero(estado.get('tempo', 0))} · CPUs: {_numero(quantidade_cpus)}", x=20, y=52)
    _elemento(raiz, "text", estado.get("motivo", ""), x=20, y=72)
    admissao = estado.get("admissao")
    if admissao:
        resultado = {True: "aceita", False: "recusada", None: "não avaliada"}.get(admissao.get("aceita"), "não avaliada")
        final = admissao.get("tempo_final")
        info = _elemento(raiz, "text", f"Admissão {resultado} · {admissao.get('metodo', '')} · fim previsto: "
                         + ("—" if final is None else _numero(final)), x=20, y=92)
        _elemento(info, "title", admissao.get("detalhe", ""))
    _elemento(raiz, "rect", x=20, y=108, width=largura - 40, height=36, rx=5,
              fill="#FEF2F2" if perdas else "#F1F5F9")
    _elemento(raiz, "text", ("ATENÇÃO: " if perdas else "") + prazos,
              id="resumo-prazos", x=32, y=132, font_size=18, font_weight="bold",
              fill="#B91C1C" if perdas else "#0F172A")
    defs = _elemento(raiz, "defs")
    padrao = _elemento(defs, "pattern", id="suspensa", width=6, height=6, patternUnits="userSpaceOnUse")
    _elemento(padrao, "rect", width=6, height=6, fill="#FFFFFF")
    _elemento(padrao, "circle", cx=3, cy=3, r=1, fill="#000000")
    padrao = _elemento(defs, "pattern", id="cpu-desligada", width=8, height=8, patternUnits="userSpaceOnUse")
    _elemento(padrao, "rect", width=8, height=8, fill="#F1F5F9")
    _elemento(padrao, "path", d="M -2,2 L 2,-2 M 0,8 L 8,0 M 6,10 L 10,6", stroke="#94A3B8", stroke_width=1)
    # Clientes conservam seu RGB em pontos; o fundo claro contrasta com reservas.
    cores_clientes = {"2563EB"} | {
        tarefa["cor"] for quadro in quadros for tarefa in quadro["tarefas"]
        if tarefa.get("tipo") in {"aperiodica", "esporadica"}
    }
    for cor in sorted(cores_clientes):
        padrao = _elemento(defs, "pattern", id="cliente-" + cor, width=6, height=6, patternUnits="userSpaceOnUse")
        _elemento(padrao, "rect", width=6, height=6, fill="#FFFFFF")
        _elemento(padrao, "circle", cx=3, cy=3, r=1.5, fill="#" + cor)
    for tipo, (rotulo, desenho, cor) in _EVENTOS.items():
        simbolo = _elemento(defs, "g", id="evento-" + tipo)
        _elemento(simbolo, "path", d=desenho, fill="none", stroke=cor, stroke_width=2, stroke_linecap="round", stroke_linejoin="round")

    # Marcas do eixo são esparsas em históricos extensos, usando só inteiros.
    passo_eixo = max(1, (fim + 11) // 12)
    ticks = sorted(set(range(0, fim + 1, passo_eixo)) | {fim})
    grade = _elemento(raiz, "g", id="eixos")
    for tempo in ticks:
        x = esquerda + tempo * _PASSO
        _elemento(grade, "line", x1=x, y1=148, x2=x, y2=eixo, stroke="#E2E8F0")
        _elemento(grade, "text", _numero(tempo), x=x, y=eixo + 19, text_anchor="middle", data_tick=tempo)
    _elemento(grade, "line", x1=esquerda, x2=direita, y1=eixo, y2=eixo, stroke="#334155")
    _elemento(grade, "text", "Tempo (ticks)", x=esquerda, y=eixo + 38)
    grupos = {}
    for chave in chaves:
        tarefa = tarefas[chave]
        grupo = _elemento(raiz, "g", data_linha="tarefa", data_chave=chave, data_id=tarefa["id"])
        grupos[chave] = grupo
        rotulo = _elemento(grupo, "text", tarefa["id"], x=esquerda - 16, y=linhas[chave] + alturas[chave] - 8, text_anchor="end")
        detalhe = _vinculo(tarefa.get("tipo", "periodica"), tarefa.get("tipo_servidor", ""),
                           tarefa.get("servidor"), tarefa.get("cliente"))
        if tarefa.get("tipo") == "servidor":
            saldo = tarefa.get("capacidade")
            reposicoes = "; ".join(f"t={_numero(t)}: +{_numero(c)}" for t, c in tarefa.get("reposicoes", [])) or "nenhuma"
            detalhe += " · saldo: " + ("—" if saldo is None else _numero(saldo)) + " · reposições: " + reposicoes
            if "duracao" in tarefa and "periodo" in tarefa:
                detalhe += f" · C/P: {_numero(tarefa['duracao'])}/{_numero(tarefa['periodo'])}"
        _elemento(rotulo, "title", detalhe)
    grupos_cpu = {}
    for cpu, y in linhas_cpu.items():
        grupos_cpu[cpu] = _elemento(raiz, "g", data_linha="cpu", data_cpu=cpu)
        _elemento(grupos_cpu[cpu], "text", f"CPU {_numero(cpu)}", x=esquerda - 16, y=y + 24, text_anchor="end")
    if not tarefas:
        _elemento(raiz, "text", "Sem tarefas executáveis; histórico vazio." if not quadros else "Sem tarefas.", x=20, y=98)

    faixas_tarefas, faixas_cpus = {}, {}
    for quadro in quadros:
        tempo = quadro["tempo"]
        miniaturas = {tarefa["chave"]: tarefa for tarefa in quadro["tarefas"]}
        for chave, tarefa in miniaturas.items():
            _juntar(faixas_tarefas, chave, tempo, (
                tarefa["estado"], tarefa["cor"], tarefa["cpu"], tarefa["id"],
                tarefa.get("tipo", "periodica"), tarefa.get("tipo_servidor", ""),
                tarefa.get("servidor"), tarefa.get("cliente"),
            ))
        for cpu, chave in enumerate(quadro["cpus"], 1):
            tarefa = miniaturas.get(chave, tarefas.get(chave, {}))
            _juntar(faixas_cpus, cpu, tempo, (
                chave, tarefa.get("cor", "64748B"), tarefa.get("id", ""),
                tarefa.get("tipo", "periodica"), tarefa.get("tipo_servidor", ""),
                tarefa.get("servidor"), tarefa.get("cliente"),
            ))

    for chave, segmentos in faixas_tarefas.items():
        y = linhas[chave] + alturas[chave] - 22
        for inicio, final, (situacao, cor, cpu, identidade, tipo, tipo_servidor, servidor, cliente) in segmentos:
            x = esquerda + inicio * _PASSO
            largura_faixa = (final - inicio) * _PASSO
            retangulo = _elemento(grupos[chave], "rect", x=x, y=y, width=(final - inicio) * _PASSO, height=18,
                                  data_inicio=inicio, data_fim=final, data_estado=situacao, data_tipo=tipo,
                                  data_servidor=servidor or "", data_cliente=cliente or "", **_estilo(situacao, cor, tipo))
            _elemento(retangulo, "title", f"{identidade}: {situacao} [{_numero(inicio)}, {_numero(final)}) · "
                      + _vinculo(tipo, tipo_servidor, servidor, cliente))
            if situacao == "executando" and tipo == "servidor":
                _borda_servidor(grupos[chave], x, y, largura_faixa, 18)
            if situacao == "executando" and cpu is not None:
                # Acima da barra, o número também é legível para RGB preto.
                rotulo = f"CPU {_numero(cpu)}"
                largura_rotulo = min(len(rotulo) * 6, (final - inicio) * _PASSO - 4)
                _elemento(grupos[chave], "text", rotulo, x=x + 2, y=y - 3, font_size=10, data_cpu=cpu,
                          textLength=largura_rotulo, lengthAdjust="spacingAndGlyphs")
    for cpu, segmentos in faixas_cpus.items():
        for inicio, final, (chave, cor, identidade, tipo, tipo_servidor, servidor, cliente) in segmentos:
            x, y = esquerda + inicio * _PASSO, linhas_cpu[cpu] + 10
            largura_faixa = (final - inicio) * _PASSO
            estilo = {"fill": "url(#cpu-desligada)", "stroke": "#64748B"} if chave is None else _estilo("executando", cor, tipo)
            retangulo = _elemento(grupos_cpu[cpu], "rect", x=x, y=y, width=largura_faixa, height=20,
                                  data_inicio=inicio, data_fim=final, data_estado="desligada" if chave is None else "executando",
                                  data_tipo=tipo, data_cliente=cliente or "", **estilo)
            rotulo = f"{identidade} → {cliente}" if cliente is not None else str(identidade)
            _elemento(retangulo, "title", f"CPU {_numero(cpu)}: " + ("desligada" if chave is None else rotulo))
            if chave is not None:
                if tipo == "servidor":
                    _borda_servidor(grupos_cpu[cpu], x, y, largura_faixa, 20)
                # Acima da faixa, independe do brilho da cor e cabe no intervalo.
                _elemento(grupos_cpu[cpu], "text", rotulo, x=x + 2, y=y - 3, font_size=10,
                          textLength=min(len(rotulo) * 6, largura_faixa - 4), lengthAdjust="spacingAndGlyphs")

    pilhas = Counter()
    for evento in eventos:
        chave, tempo, tipo = evento["chave"], evento["tempo"], evento["tipo"]
        posicao = (chave, tempo)
        x = esquerda + tempo * _PASSO
        y = linhas[chave] + 6 + pilhas[posicao] * 14
        pilhas[posicao] += 1
        marcador = _marcador(grupos[chave], tipo, x, y, data_tipo=tipo, data_tempo=tempo, data_chave=chave)
        _elemento(marcador, "title", f"{_EVENTOS[tipo][0]} · {evento['tarefa']} · t={_numero(tempo)} · {evento.get('detalhe', '')}"
                  + (f" · CPU {_numero(evento['cpu'])}" if evento.get("cpu") is not None else ""))

    grupo_legenda = _elemento(raiz, "g", id="legenda")
    _elemento(grupo_legenda, "text", "Legenda", x=20, y=legenda, font_weight="bold")
    for indice, (tipo, (rotulo, _, _)) in enumerate(_EVENTOS.items()):
        x, y = 30 + (indice % 4) * 280, legenda + 25 + (indice // 4) * 25
        _marcador(grupo_legenda, tipo, x, y)
        _elemento(grupo_legenda, "text", rotulo, x=x + 15, y=y + 4)
    estados_legenda = [
        ("executando", "Execução (RGB da tarefa)"), ("pronta", "Pronta (sem preenchimento)"),
        ("suspensa", "Suspensa (pontos pretos)"), ("nova", "Antes da chegada"),
        ("terminada", "Após o término"), ("aguardando", "Aguardando ativação"),
    ]
    for indice, (situacao, rotulo) in enumerate(estados_legenda):
        x, y = 20 + (indice % 3) * 360, base_estados + (indice // 3) * 25
        _elemento(grupo_legenda, "rect", x=x, y=y, width=20, height=14, **_estilo(situacao, "2563EB"))
        _elemento(grupo_legenda, "text", rotulo, x=x + 28, y=y + 12)
    y = base_estados + 54
    _elemento(grupo_legenda, "rect", x=20, y=y, width=20, height=14, **_estilo("executando", "2563EB", "servidor"))
    _borda_servidor(grupo_legenda, 20, y, 20, 14)
    _elemento(grupo_legenda, "text", "Servidor (borda dupla)", x=48, y=y + 12)
    _elemento(grupo_legenda, "rect", x=380, y=y, width=20, height=14, **_estilo("executando", "2563EB", "aperiodica"))
    _elemento(grupo_legenda, "text", "Cliente (cor pontilhada, borda tracejada)", x=408, y=y + 12)
    _elemento(grupo_legenda, "text", "CPU: servidor → cliente", x=740, y=y + 12)
    _elemento(grupo_legenda, "rect", x=20, y=y + 28, width=20, height=14, fill="url(#cpu-desligada)", stroke="#64748B")
    _elemento(grupo_legenda, "text", "CPU desligada (hachura cinza)", x=48, y=y + 40)
    return '<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(raiz, encoding="unicode")


def salvar_svg(estado: dict, caminho) -> Path:
    """Grava no destino explícito; a interface decide a confirmação de troca."""
    documento = gerar_svg(estado)
    try:
        destino = Path(caminho)
        destino.write_text(documento, encoding="utf-8")
    except (OSError, ValueError) as erro:
        raise ErroSimulacao(f"Não foi possível salvar o SVG em {caminho!s}: {erro}") from erro
    return destino
