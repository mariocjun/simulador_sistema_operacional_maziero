"""Interface didática: converte entradas e apresenta apenas o contrato público.

O ponto de montagem fornece Aplicacao(root, api, carregar, salvar_svg). Importar
este módulo não abre janelas, configura sessões nem cria pastas de exportação.
"""

from bisect import bisect_left, bisect_right
from collections import defaultdict
from dataclasses import asdict
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from queue import Empty, Queue
import re
from threading import Thread
import tkinter as tk
from tkinter import colorchooser, filedialog, messagebox, ttk

from contratos import Configuracao, DefinicaoTarefa


CORES = ("2563EB", "DC2626", "15803D", "9333EA", "B45309", "0891B2", "BE185D")
ESTADOS = {
    "nova": "Ainda não chegou", "pronta": "Pronta", "executando": "Executando",
    "suspensa": "Suspensa", "terminada": "Terminada", "encerrada": "Encerrada",
    "aguardando": "Aguardando ativação", "desligada": "Desligada",
}
EVENTOS = {
    "chegada": ("↓", "Chegada", "#0369a1"),
    "ativacao": ("↑", "Ativação", "#15803d"),
    "fim_instancia": ("✓", "Fim de instância", "#15803d"),
    "termino": ("■", "Término da tarefa", "#334155"),
    "prazo": ("!", "Perda de prazo", "#b91c1c"),
    "sorteio": ("★", "Sorteio", "#7e22ce"),
    "preempcao": ("↔", "Preempção", "#a16207"),
    "manual": ("M", "Intervenção manual", "#9d174d"),
}


def chave_natural(identificador):
    """Compara números como números: 10 > 2 e T10 > T2, sem distinguir caixa."""
    identidade = str(identificador).casefold()
    if re.fullmatch(r"[+-]?\d+", identidade):
        return ((0, Decimal(identidade)),)
    return tuple((0, Decimal(parte)) if parte.isdecimal() else (1, parte)
                 for parte in re.split(r"(\d+)", identidade) if parte)


def inteiro(texto, campo, minimo=0):
    try:
        valor = int(texto)
    except (ValueError, TypeError):
        if isinstance(texto, str) and re.fullmatch(r"[+-]?\d+", texto.strip()):
            valor = int(Decimal(texto.strip()))
        else:
            raise ValueError(f"{campo}: informe um número inteiro.") from None
    if valor < minimo:
        raise ValueError(f"{campo}: o valor deve ser maior ou igual a {minimo}.")
    return valor


def texto(valor):
    """Não recusa tempos válidos pelo limite de conversão decimal do Python."""
    try:
        return str(valor)
    except ValueError:
        if isinstance(valor, int):
            return format(Decimal(valor), "f")
        raise


def valor_visivel(valor):
    return "—" if valor is None else texto(valor)


def cor_tk(cor):
    return "#" + cor.lstrip("#")


def cor_texto(cor):
    rgb = cor.lstrip("#")
    brilho = sum(int(rgb[i:i + 2], 16) * peso
                 for i, peso in ((0, .299), (2, .587), (4, .114)))
    return "#111827" if brilho > 155 else "#ffffff"


def tipo_resultado(estado):
    if estado["situacao"] != "concluida":
        return "parcial"
    # Término manual não transforma trabalho pendente em dez conclusões.
    if any(t["estado"] == "terminada" and t["concluidas"] < 10 for t in estado["tarefas"]):
        return "encerrada-manualmente"
    return "concluida"


def comprimir_quadros(quadros):
    """Une intervalos adjacentes iguais, sem perder CPU, cor ou identidade antiga.

    Cada faixa contém (início, fim, (estado, cpu, cor, id)). Não se cria um item
    Canvas por tick. Lacunas e mudanças históricas continuam separadas.
    """
    faixas = defaultdict(list)

    def adicionar(linha, tempo, dados):
        trechos = faixas[linha]
        if trechos and trechos[-1][1] == tempo and trechos[-1][2] == dados:
            inicio, _, anteriores = trechos[-1]
            trechos[-1] = (inicio, tempo + 1, anteriores)
        else:
            trechos.append((tempo, tempo + 1, dados))

    for quadro in quadros:
        tempo = quadro["tempo"]
        tarefas = {t["chave"]: t for t in quadro["tarefas"]}
        for chave, tarefa in tarefas.items():
            adicionar(("tarefa", chave), tempo,
                      (tarefa["estado"], tarefa["cpu"], tarefa["cor"], tarefa["id"]))
        for numero, chave in enumerate(quadro["cpus"], 1):
            if chave is None:
                dados = ("desligada", numero, "CBD5E1", "")
            else:
                tarefa = tarefas[chave]
                dados = ("executando", numero, tarefa["cor"], tarefa["id"])
            adicionar(("cpu", numero), tempo, dados)
    return dict(faixas)


class Gantt(ttk.Frame):
    """Histórico rolável, com régua/nomes fixos e desenho só da área visível."""

    altura_linha = 48

    def __init__(self, parent):
        super().__init__(parent)
        self.faixas = {}
        self.inicios = {}
        self.linhas = []
        self.eventos = {}
        self.tempos_eventos = {}
        self.fim = 1
        self.escala = 28.0
        self._pendente = None
        self._dicas = {}
        self.columnconfigure(1, weight=1)
        self.rowconfigure(1, weight=1)

        barra = ttk.Frame(self)
        barra.grid(row=0, column=0, columnspan=3, sticky="ew", pady=(0, 4))
        ttk.Label(barra, text="Histórico de execução", font=("Segoe UI", 11, "bold")).pack(side="left")
        ttk.Button(barra, text="Ajustar ao tempo total", command=self.ajustar).pack(side="right")
        self.zoom = tk.StringVar(self, "28")
        combo = ttk.Combobox(barra, textvariable=self.zoom, values=(2, 5, 10, 28, 50, 100),
                             width=7, state="readonly")
        combo.pack(side="right", padx=6)
        combo.bind("<<ComboboxSelected>>", self._mudar_zoom)
        ttk.Label(barra, text="Pixels/tick:").pack(side="right")

        self.rotulos = tk.Canvas(self, width=142, background="#f1f5f9", highlightthickness=0)
        self.rotulos.grid(row=1, column=0, sticky="ns")
        self.canvas = tk.Canvas(self, background="white", highlightthickness=0,
                                xscrollincrement=1, yscrollincrement=1)
        self.canvas.grid(row=1, column=1, sticky="nsew")
        self.regua = tk.Canvas(self, height=27, background="#f1f5f9", highlightthickness=0)
        self.regua.grid(row=2, column=1, sticky="ew")
        ttk.Label(self, text="Tempo (ticks) →").grid(row=2, column=0, sticky="e", padx=6)
        self.horizontal = ttk.Scrollbar(self, orient="horizontal", command=self._rolar_x)
        self.horizontal.grid(row=3, column=1, sticky="ew")
        self.vertical = ttk.Scrollbar(self, orient="vertical", command=self._rolar_y)
        self.vertical.grid(row=1, column=2, sticky="ns")
        self.canvas.configure(xscrollcommand=self._posicao_x, yscrollcommand=self._posicao_y)
        self.canvas.bind("<Configure>", self._agendar)
        self.canvas.bind("<Motion>", self._mostrar_dica)
        self.canvas.bind("<Leave>", lambda event: self.dica.set(self.dica_padrao))
        for widget in (self.canvas, self.rotulos):
            widget.bind("<MouseWheel>", self._roda)
            widget.bind("<Shift-MouseWheel>", self._roda_horizontal)
            widget.bind("<Button-4>", lambda event: self._rolar_y("scroll", -48, "units"))
            widget.bind("<Button-5>", lambda event: self._rolar_y("scroll", 48, "units"))

        legenda = ("Sólido + CPU: execução (C1 = CPU 1)   •   Contorno vazio: pronta   •   Pontos pretos: suspensa\n"
                   "Cinza pontilhado: CPU desligada   •   Traço cinza: ainda não chegou   •   ×: terminada\n"
                   + "   ·   ".join(f"{s} {nome}" for s, nome, _ in EVENTOS.values()))
        self.legenda = ttk.Label(self, text=legenda, wraplength=1100, justify="left")
        self.legenda.grid(row=4, column=0, columnspan=3, sticky="w", pady=(5, 0))
        self.dica_padrao = "Passe o mouse sobre um intervalo ou símbolo para consultar os detalhes."
        self.dica = tk.StringVar(self, self.dica_padrao)
        ttk.Label(self, textvariable=self.dica, wraplength=1100).grid(
            row=5, column=0, columnspan=3, sticky="w")
        self.bind("<Destroy>", self._destruir, add="+")

    def _destruir(self, event):
        if event.widget == self and self._pendente is not None:
            self.after_cancel(self._pendente)
            self._pendente = None

    def atualizar(self, estado):
        quadros = estado["quadros"]
        self.faixas = comprimir_quadros(quadros)
        self.inicios = {chave: [s[0] for s in trechos] for chave, trechos in self.faixas.items()}
        # A chave estável liga o passado ao TCB mesmo se o ID foi editado.
        tarefas = {}
        for quadro in quadros:
            tarefas.update((t["chave"], t) for t in quadro["tarefas"])
        tarefas.update((t["chave"], t) for t in estado["tarefas"])
        n_cpus = max([estado["quantidade_cpus"]] + [len(q["cpus"]) for q in quadros])
        outros = {e["chave"]: e["tarefa"] for e in estado["eventos"] if e["chave"] not in tarefas}
        self.linhas = [(("tarefa", chave), nome) for chave, nome in outros.items()]
        self.linhas += [(("cpu", n), f"CPU {n}" + (
            " (histórica)" if n > estado["quantidade_cpus"] else "")) for n in range(1, n_cpus + 1)]
        self.linhas += [(("tarefa", t["chave"]), t["id"]) for t in sorted(
            tarefas.values(), key=lambda t: chave_natural(t["id"]), reverse=True)]
        agrupados = defaultdict(lambda: defaultdict(list))
        for evento in estado["eventos"]:
            agrupados[("tarefa", evento["chave"])][evento["tempo"]].append(evento)
        self.eventos = {chave: dict(tempos) for chave, tempos in agrupados.items()}
        self.tempos_eventos = {chave: sorted(tempos) for chave, tempos in self.eventos.items()}
        self.fim = max([1, estado["tempo"]] + [q["tempo"] + 1 for q in quadros]
                       + [e["tempo"] for e in estado["eventos"]])
        self._dimensoes()

    def _dimensoes(self):
        largura = self.fim * self.escala + 35
        altura = max(1, len(self.linhas)) * self.altura_linha
        self.canvas.configure(scrollregion=(0, 0, largura, altura))
        self.rotulos.configure(scrollregion=(0, 0, 142, altura))
        self.regua.configure(scrollregion=(0, 0, largura, 27))
        self._agendar()

    def _mudar_zoom(self, event=None):
        tempo_esquerda = max(0, self.canvas.canvasx(0)) / self.escala
        self.escala = float(self.zoom.get())
        self._dimensoes()
        self.canvas.xview_moveto(tempo_esquerda * self.escala / (self.fim * self.escala + 35))

    def ajustar(self):
        self.escala = max(1, self.canvas.winfo_width() - 35) / self.fim
        self.zoom.set(f"{self.escala:.4g}")
        self._dimensoes()
        self._rolar_x("moveto", 0)

    def _posicao_x(self, inicio, fim):
        self.horizontal.set(inicio, fim)
        self.regua.xview_moveto(inicio)
        self._agendar()

    def _posicao_y(self, inicio, fim):
        self.vertical.set(inicio, fim)
        self.rotulos.yview_moveto(inicio)
        self._agendar()

    def _rolar_x(self, *args):
        self.canvas.xview(*args)
        self._agendar()

    def _rolar_y(self, *args):
        self.canvas.yview(*args)
        self._agendar()

    def _roda(self, event):
        self._rolar_y("scroll", -int(event.delta / 120 or (1 if event.delta > 0 else -1)) * 48, "units")
        return "break"

    def _roda_horizontal(self, event):
        self._rolar_x("scroll", -int(event.delta / 120 or (1 if event.delta > 0 else -1)) * 80, "units")
        return "break"

    def _agendar(self, event=None):
        if self._pendente is None:
            self._pendente = self.after_idle(self.desenhar)

    def _mostrar_dica(self, event):
        itens = self.canvas.find_withtag("current")
        self.dica.set(self._dicas.get(itens[0], self.dica_padrao) if itens else self.dica_padrao)

    def desenhar(self):
        if self._pendente is not None:
            self.after_cancel(self._pendente)
            self._pendente = None
        self.canvas.delete("all")
        self.rotulos.delete("all")
        self.regua.delete("all")
        self._dicas.clear()
        esquerda = max(0, self.canvas.canvasx(0))
        direita = esquerda + self.canvas.winfo_width()
        topo = max(0, self.canvas.canvasy(0))
        baixo = topo + self.canvas.winfo_height()
        inicio_tempo, fim_tempo = esquerda / self.escala, direita / self.escala
        # Uma régua legível, sem percorrer todos os ticks de uma execução longa.
        passo = 1
        while passo * self.escala < 70:
            passo *= 10
        if passo > 1 and passo / 5 * self.escala >= 70:
            passo //= 5
        elif passo > 1 and passo / 2 * self.escala >= 70:
            passo //= 2
        for tempo in range(int(inicio_tempo // passo) * passo, int(fim_tempo) + passo, passo):
            if tempo > self.fim:
                break
            x = tempo * self.escala
            self.canvas.create_line(x, topo, x, baixo, fill="#e2e8f0", tags="grade")
            self.regua.create_line(x, 0, x, 6, fill="#64748b")
            self.regua.create_text(x + 3, 15, text=str(tempo), anchor="w", fill="#334155")
        primeira = int(topo // self.altura_linha)
        ultima = min(len(self.linhas), int(baixo // self.altura_linha) + 1)
        for numero in range(primeira, ultima):
            chave, nome = self.linhas[numero]
            y = numero * self.altura_linha
            self.rotulos.create_text(134, y + 29, text=nome, anchor="e", width=130,
                                      fill="#0f172a", tags="rotulo")
            self.canvas.create_line(esquerda, y + 47, direita, y + 47, fill="#e2e8f0")
            trechos = self.faixas.get(chave, [])
            comeco = max(0, bisect_right(self.inicios.get(chave, []), inicio_tempo) - 1)
            for indice in range(comeco, len(trechos)):
                inicio, fim, dados = trechos[indice]
                if inicio > fim_tempo:
                    break
                if fim < inicio_tempo:
                    continue
                self._intervalo(chave, y, inicio, fim, dados, esquerda, direita)
            tempos = self.tempos_eventos.get(chave, [])
            for tempo in tempos[bisect_left(tempos, inicio_tempo):bisect_right(tempos, fim_tempo)]:
                self._evento(chave, y, tempo)
        if not self.faixas:
            self.canvas.create_text(esquerda + 18, topo + 20, anchor="nw", fill="#475569",
                                    text="Use Passo +1 ou Completo para desenhar os intervalos.\n"
                                         "Carregue um arquivo ou crie tarefas para começar.", tags="orientacao")

    def _intervalo(self, chave, y, inicio, fim, dados, esquerda, direita):
        estado, cpu, cor, identificador = dados
        x1 = max(esquerda - 5, inicio * self.escala)
        x2 = min(direita + 5, fim * self.escala)
        y1, y2 = y + 19, y + 43
        cor = cor_tk(cor)
        tags = ("intervalo", estado, f"{chave[0]}:{chave[1]}")
        itens = []
        texto, frente = "", "#475569"
        if estado == "executando":
            itens.append(self.canvas.create_rectangle(x1, y1, x2, y2, fill=cor, outline=cor, tags=tags))
            texto = f"CPU {cpu}" if chave[0] == "tarefa" else identificador
            frente = cor_texto(cor)
        elif estado == "pronta":
            itens.append(self.canvas.create_rectangle(x1, y1, x2, y2, fill="", outline=cor, width=2, tags=tags))
        elif estado == "suspensa":
            itens.append(self.canvas.create_rectangle(x1, y1, x2, y2, fill="white", outline="#64748b", tags=tags))
            # Pontos pretos literais, alinhados mesmo ao rolar horizontalmente.
            for x in range(int(x1 // 9) * 9 + 5, int(x2), 9):
                for yp in (y1 + 5, y1 + 14):
                    itens.append(self.canvas.create_oval(x, yp, min(x + 2, x2), yp + 2,
                                                        fill="black", outline="black", tags=tags + ("ponto",)))
        elif estado == "desligada":
            itens.append(self.canvas.create_rectangle(x1, y1, x2, y2, fill="#94a3b8",
                                                      stipple="gray25", outline="#94a3b8", tags=tags))
            texto = "Desligada"
        else:
            # Ausência, espera por ativação e término não imitam fila de prontos.
            itens.append(self.canvas.create_line(x1, y + 31, x2, y + 31,
                                                 fill="#94a3b8", dash=(4, 4), tags=tags))
            texto = "× Terminada" if estado in ("terminada", "encerrada") else ESTADOS.get(estado, estado)
        if estado == "executando" and chave[0] == "tarefa" and x2 - x1 <= 52:
            texto = f"C{cpu}"
        if texto and (x2 - x1 > 52 or (estado == "executando" and x2 - x1 >= 18)):
            itens.append(self.canvas.create_text((x1 + x2) / 2, y + 31, text=texto,
                                                 fill=frente, font=("Segoe UI", 9), tags=tags))
        dica = (f"{identificador or 'CPU ' + str(cpu)} · [{inicio}, {fim}) · "
                f"{ESTADOS.get(estado, estado)}" + (f" · CPU {cpu}" if cpu is not None else ""))
        for item in itens:
            self._dicas[item] = dica

    def _evento(self, chave, y, tempo):
        eventos = self.eventos[chave][tempo]
        simbolos = "".join(dict.fromkeys(EVENTOS.get(e["tipo"], ("?", "", ""))[0] for e in eventos))
        cor = "#b91c1c" if any(e["tipo"] == "prazo" for e in eventos) else "#334155"
        x = tempo * self.escala
        linha = self.canvas.create_line(x, y + 17, x, y + 44, fill=cor, dash=(2, 2), tags="evento")
        item = self.canvas.create_text(x, y + 9, text=simbolos, fill=cor,
                                       font=("Segoe UI", 10, "bold"), tags="evento")
        dica = f"t={tempo} · " + " | ".join(
            f"{e['tarefa']}: {EVENTOS.get(e['tipo'], ('?', e['tipo'], ''))[1]} — {e['detalhe']}"
            for e in eventos)
        self._dicas[item] = self._dicas[linha] = dica


class Aplicacao:
    """Recebe serviços; não importa API concreta, motor ou modelos internos."""

    def __init__(self, root, api, carregar, salvar_svg, *, configuracao_carregada=False):
        self.root = root
        self.api = api
        self.carregar = carregar
        self.salvar_svg = salvar_svg
        self.estado = None
        self.ocupada = False
        self.pasta_saida = Path.cwd()
        self._controles = []
        self._fila = Queue()
        self._consulta_pendente = None
        self._fechar_ao_terminar = False
        self._dialogo = None
        self._geracao = 0
        self._exportado = None
        self.root.title("Simulador RM/EDF — laboratório de escalonamento")
        self.root.geometry("1280x860")
        self.root.minsize(980, 720)
        self.root.protocol("WM_DELETE_WINDOW", self.fechar)
        self.root.bind("<Destroy>", self._ao_destruir, add="+")
        self._construir()
        try:
            self._atualizar(self.api.consultar_estado(), "Sessão pronta. Carregue um arquivo ou crie uma tarefa.",
                            exportar=configuracao_carregada)
        except Exception as erro:
            self._erro("Não foi possível consultar a sessão", erro)
            self._habilitar()

    def _botao(self, parent, texto, comando, **pack):
        botao = ttk.Button(parent, text=texto, command=comando)
        botao.pack(side="left", padx=(0, 5), **pack)
        self._controles.append((botao, "normal"))
        return botao

    def _construir(self):
        principal = ttk.Frame(self.root, padding=12)
        principal.pack(fill="both", expand=True)
        ttk.Label(principal, text="Simulador RM / EDF", font=("Segoe UI", 17, "bold")).pack(anchor="w")
        ttk.Label(principal, text="Explore prioridades, acompanhe cada instância e compare decisões no tempo.").pack(anchor="w")
        barra = ttk.Frame(principal)
        barra.pack(fill="x", pady=(10, 7))
        self._botao(barra, "Carregar arquivo…", self.carregar_arquivo)
        self._botao(barra, "Nova sessão", self.nova_sessao)
        self.reinicio = self._botao(barra, "Reiniciar", self.reiniciar)
        ttk.Separator(barra, orient="vertical").pack(side="left", fill="y", padx=5)
        self.retrocesso = self._botao(barra, "← Voltar", self.voltar)
        self.passo = self._botao(barra, "Passo +1 →", self.avancar)
        self.completo = self._botao(barra, "Completo", self.executar_completo)
        self.exportacao = self._botao(barra, "Salvar SVG como…", self.exportar_como)
        self.progresso = ttk.Progressbar(barra, mode="indeterminate", length=75)
        self.progresso.pack(side="right", padx=6)

        parametros = ttk.LabelFrame(principal, text="Parâmetros da sessão", padding=7)
        parametros.pack(fill="x")
        linha = ttk.Frame(parametros)
        linha.pack(fill="x")
        self.algoritmo = tk.StringVar(self.root, "RM")
        self.cpus = tk.StringVar(self.root, "1")
        self.quantum = tk.StringVar(self.root, "0")
        for texto, variavel, valores in (("Algoritmo", self.algoritmo, ("RM", "EDF")),
                                        ("CPUs", self.cpus, None), ("Quantum", self.quantum, None)):
            ttk.Label(linha, text=texto + ":").pack(side="left", padx=(0, 5))
            if valores:
                entrada = ttk.Combobox(linha, textvariable=variavel, values=valores, width=7, state="readonly")
                normal = "readonly"
            else:
                entrada = ttk.Entry(linha, textvariable=variavel, width=9)
                normal = "normal"
            entrada.pack(side="left", padx=(0, 14))
            self._controles.append((entrada, normal))
        self.aplicar = self._botao(linha, "Aplicar parâmetros", self.aplicar_parametros)
        ttk.Label(linha, text="Quantum é preservado, mas ignorado pelo escalonamento RM/EDF.").pack(side="left", padx=5)
        self.regra = tk.StringVar(self.root)
        ttk.Label(parametros, textvariable=self.regra, wraplength=1100).pack(anchor="w", pady=(5, 0))

        saida = ttk.Frame(principal)
        saida.pack(fill="x", pady=6)
        ttk.Label(saida, text="Pasta dos SVGs automáticos:").pack(side="left")
        self.destino = tk.StringVar(self.root, str(self.pasta_saida))
        ttk.Entry(saida, textvariable=self.destino, state="readonly").pack(side="left", fill="x", expand=True, padx=6)
        self._botao(saida, "Escolher pasta…", self.escolher_pasta)
        self.resumo = tk.StringVar(self.root, "Consultando sessão…")
        ttk.Label(principal, textvariable=self.resumo, font=("Segoe UI", 11, "bold"),
                  wraplength=1150).pack(anchor="w", pady=(0, 5))

        paineis = ttk.Panedwindow(principal, orient="vertical")
        paineis.pack(fill="both", expand=True)
        topo = ttk.Frame(paineis, height=225)
        paineis.add(topo, weight=2)
        self.abas = ttk.Notebook(topo)
        self.abas.pack(fill="both", expand=True)
        aba_tcb = ttk.Frame(self.abas, padding=5)
        aba_instancias = ttk.Frame(self.abas, padding=5)
        aba_cpus = ttk.Frame(self.abas, padding=5)
        aba_eventos = ttk.Frame(self.abas, padding=5)
        for frame, texto in ((aba_tcb, "Tarefas · TCB"), (aba_instancias, "Instâncias da tarefa"),
                             (aba_cpus, "CPUs agora"), (aba_eventos, "Registro de eventos")):
            self.abas.add(frame, text=texto)
        acoes = ttk.Frame(aba_tcb)
        acoes.pack(fill="x", pady=(0, 5))
        self.criacao = self._botao(acoes, "+ Criar tarefa…", self.criar_tarefa)
        self.edicao = self._botao(acoes, "Editar…", self.editar_tarefa)
        self.suspensao = self._botao(acoes, "Suspender", lambda: self.alterar_estado("suspensa"))
        self.retomada = self._botao(acoes, "Tornar pronta", lambda: self.alterar_estado("pronta"))
        self.espera = self._botao(acoes, "Aguardar ativação", lambda: self.alterar_estado("aguardando"))
        self.forcar = self._botao(acoes, "Executar na CPU", self.executar_na_cpu)
        self.cpu_manual = tk.StringVar(self.root, "1")
        entrada_cpu = ttk.Entry(acoes, textvariable=self.cpu_manual, width=5)
        entrada_cpu.pack(side="left", padx=(0, 10))
        self._controles.append((entrada_cpu, "normal"))
        self.termino = self._botao(acoes, "Terminar tarefa", lambda: self.alterar_estado("terminada"))
        colunas = (("id", "ID", 65), ("chave", "Chave", 55), ("cor", "RGB", 75),
                   ("estado", "Estado", 150), ("cpu", "CPU", 50), ("ingresso", "Ingresso", 65),
                   ("duracao", "Duração", 65), ("periodo", "Período", 65), ("prazo", "Prazo relativo", 100),
                   ("concluidas", "Concluídas / 10", 100), ("liberadas", "Liberadas", 70),
                   ("pendentes", "Pendentes", 75), ("execucao", "Ticks executados", 110),
                   ("proxima_ativacao", "Próxima ativação", 115), ("eventos", "Eventos configurados", 180))
        self.tcb = self._tabela(aba_tcb, colunas)
        self.tcb.bind("<<TreeviewSelect>>", self._selecionar)
        self.tcb.bind("<Double-1>", lambda event: self.editar_tarefa())
        self.detalhe = tk.StringVar(self.root, "Selecione uma tarefa no TCB.")
        ttk.Label(aba_instancias, textvariable=self.detalhe, wraplength=1080).pack(anchor="w", pady=(0, 5))
        self.instancias = self._tabela(aba_instancias, (
            ("numero", "Instância", 80), ("ativacao", "Ativação", 90), ("deadline", "Deadline absoluto", 130),
            ("duracao", "Duração original", 115), ("executado", "Executado", 90),
            ("restante", "Restante", 90), ("fim", "Concluída em", 100), ("perdeu_prazo", "Perdeu prazo?", 110),
            ("periodo", "Período original / RM", 145)))
        ttk.Label(aba_cpus, text="Alocação do retrato atual; o Gantt conserva as alocações dos intervalos anteriores.").pack(anchor="w")
        self.tabela_cpus = self._tabela(aba_cpus, (("numero", "CPU", 90), ("tarefa", "Tarefa", 160),
                                                 ("estado", "Situação", 180)))
        self.tabela_eventos = self._tabela(aba_eventos, (("tempo", "Tempo", 70), ("tarefa", "Tarefa", 100),
            ("tipo", "Evento", 175), ("cpu", "CPU", 60), ("detalhe", "Motivo / detalhes", 650)))
        self.gantt = Gantt(paineis)
        paineis.add(self.gantt, weight=3)

        rodape = ttk.LabelFrame(principal, text="Mensagens · avisos · arquivos gerados", padding=5)
        rodape.pack(fill="x", pady=(7, 0))
        self.mensagens = tk.Text(rodape, height=3, wrap="word", state="disabled", font=("Segoe UI", 9))
        rolagem = ttk.Scrollbar(rodape, command=self.mensagens.yview)
        rolagem.pack(side="right", fill="y")
        self.mensagens.configure(yscrollcommand=rolagem.set)
        self.mensagens.pack(fill="x", expand=True)

    @staticmethod
    def _tabela(parent, colunas):
        frame = ttk.Frame(parent)
        frame.pack(fill="both", expand=True)
        frame.rowconfigure(0, weight=1)
        frame.columnconfigure(0, weight=1)
        tabela = ttk.Treeview(frame, columns=[c[0] for c in colunas], show="headings", selectmode="browse", height=5)
        for chave, nome, largura in colunas:
            tabela.heading(chave, text=nome)
            tabela.column(chave, width=largura, minwidth=45, stretch=False, anchor="w")
        vertical = ttk.Scrollbar(frame, orient="vertical", command=tabela.yview)
        horizontal = ttk.Scrollbar(frame, orient="horizontal", command=tabela.xview)
        tabela.configure(yscrollcommand=vertical.set, xscrollcommand=horizontal.set)
        tabela.grid(row=0, column=0, sticky="nsew")
        vertical.grid(row=0, column=1, sticky="ns")
        horizontal.grid(row=1, column=0, sticky="ew")
        return tabela

    @staticmethod
    def _limpar(tabela):
        filhos = tabela.get_children()
        if filhos:
            tabela.delete(*filhos)

    @staticmethod
    def _inserir(tabela, valores, **opcoes):
        # O Tk recebe texto pronto, inclusive para inteiros com milhares de dígitos.
        tabela.insert("", "end", values=tuple(valor_visivel(v) for v in valores), **opcoes)

    def _mensagem(self, texto):
        self.mensagens.configure(state="normal")
        self.mensagens.insert("end", texto + "\n")
        self.mensagens.see("end")
        self.mensagens.configure(state="disabled")

    def _erro(self, contexto, erro):
        texto = f"{contexto}: {erro}"
        self._mensagem(texto)
        pai = self._dialogo if self._dialogo is not None and self._dialogo.winfo_exists() else self.root
        messagebox.showerror("Operação não concluída", texto, parent=pai)

    def _habilitar(self):
        for widget, normal in self._controles:
            widget.configure(state="disabled" if self.ocupada else normal)
        if self.ocupada:
            return
        if self.estado is None:
            for widget in (self.reinicio, self.retrocesso, self.passo, self.completo, self.exportacao,
                           self.aplicar, self.criacao, self.edicao, self.suspensao, self.retomada, self.espera, self.forcar, self.termino):
                widget.configure(state="disabled")
            return
        self.retrocesso.configure(state="normal" if self.estado["pode_voltar"] else "disabled")
        final = self.estado["situacao"] == "concluida"
        self.passo.configure(state="disabled" if final and not self.estado["pode_refazer"] else "normal")
        self.completo.configure(state="disabled" if final else "normal")
        selecionada = bool(self.tcb.selection())
        for widget in (self.edicao, self.suspensao, self.retomada, self.espera, self.forcar, self.termino):
            widget.configure(state="normal" if selecionada else "disabled")

    def _atualizar(self, estado, mensagem="Estado atualizado.", exportar=True):
        self.estado = estado
        self.algoritmo.set(estado["algoritmo"])
        self.cpus.set(texto(estado["quantidade_cpus"]))
        self.quantum.set(texto(estado["quantum"]))
        self.regra.set("RM: menor período tem maior prioridade." if estado["algoritmo"] == "RM"
                       else "EDF: a instância com o deadline absoluto mais próximo tem maior prioridade.")
        situacao = {"concluida": "Concluída", "interrompida": "Interrompida",
                    "em_andamento": "Em andamento"}.get(estado["situacao"], estado["situacao"])
        if tipo_resultado(estado) == "encerrada-manualmente":
            situacao = "Encerrada com término manual (não equivale a dez conclusões)"
        self.resumo.set(f"t = {texto(estado['tempo'])} ticks  ·  {situacao}  ·  "
                       f"{len(estado['tarefas'])} tarefas  ·  {estado['quantidade_cpus']} CPUs"
                       + (f"  ·  {estado['motivo']}" if estado["motivo"] else ""))
        selecionada = self.tcb.selection()
        y_tcb = self.tcb.yview()[0]
        self._limpar(self.tcb)
        for t in sorted(estado["tarefas"], key=lambda t: chave_natural(t["id"]), reverse=True):
            progresso = sum(i["executado"] for i in t["instancias"])
            self._inserir(self.tcb, (
                t["id"], t["chave"], t["cor"], ESTADOS.get(t["estado"], t["estado"]), valor_visivel(t["cpu"]),
                t["ingresso"], t["duracao"], t["periodo"], t["prazo"], f"{t['concluidas']} / 10", t["liberadas"],
                t["pendentes"], progresso, valor_visivel(t["proxima_ativacao"]), t["eventos"] or "Nenhum"), iid=str(t["chave"]))
        if selecionada and self.tcb.exists(selecionada[0]):
            self.tcb.selection_set(selecionada[0])
        elif self.tcb.get_children():
            self.tcb.selection_set(self.tcb.get_children()[0])
        self.tcb.yview_moveto(y_tcb)
        self._selecionar()
        self._limpar(self.tabela_cpus)
        tarefas = {t["chave"]: t for t in estado["tarefas"]}
        for numero, chave in enumerate(estado["cpus"], 1):
            self._inserir(self.tabela_cpus, (numero, tarefas[chave]["id"] if chave is not None else "—",
                                            "Executando" if chave is not None else "Desligada"))
        self._limpar(self.tabela_eventos)
        for evento in estado["eventos"]:
            simbolo, nome, _ = EVENTOS.get(evento["tipo"], ("?", evento["tipo"], ""))
            self._inserir(self.tabela_eventos, (evento["tempo"], evento["tarefa"], f"{simbolo} {nome}",
                                               valor_visivel(evento["cpu"]), evento["detalhe"]))
        self.gantt.atualizar(estado)
        self._habilitar()
        self._mensagem(mensagem)
        if estado["motivo"]:
            self._mensagem(estado["motivo"])
        for aviso in estado["avisos"]:
            self._mensagem("Aviso: " + aviso)
        assinatura = (self._geracao, estado["tempo"])
        if exportar and estado["situacao"] == "concluida" and self._exportado != assinatura:
            if self.exportar_automaticamente():
                self._exportado = assinatura

    def tarefa_selecionada(self):
        selecao = self.tcb.selection()
        if not selecao or self.estado is None:
            return None
        return next((t for t in self.estado["tarefas"] if str(t["chave"]) == selecao[0]), None)

    def _selecionar(self, event=None):
        tarefa = self.tarefa_selecionada()
        self._limpar(self.instancias)
        if tarefa is None:
            self.detalhe.set("Crie uma tarefa ou selecione uma linha do TCB para consultar suas instâncias.")
        else:
            self.detalhe.set(
                f"{tarefa['id']} · chave {tarefa['chave']} · {ESTADOS.get(tarefa['estado'], tarefa['estado'])}\n"
                f"Definição atual: duração {texto(tarefa['duracao'])}, período {texto(tarefa['periodo'])}, prazo relativo {texto(tarefa['prazo'])}. "
                "Tempos editados valem para novas instâncias; as liberadas mantêm duração, deadline e período/prioridade RM abaixo.")
            for i in tarefa["instancias"]:
                self._inserir(self.instancias, (i["numero"], i["ativacao"], i["deadline"], i["duracao"],
                    i["executado"], max(0, i["duracao"] - i["executado"]), valor_visivel(i["fim"]),
                    "Sim" if i["perdeu_prazo"] else "Não", i["periodo"]))
        self._habilitar()

    def _chamar(self, metodo, *args, edicao=False, **kwargs):
        if self.ocupada:
            return False
        try:
            estado = getattr(self.api, metodo)(*args, **kwargs)
        except Exception as erro:
            self._erro("Comando recusado", erro)
            return False
        if edicao:
            self._geracao += 1
        self._atualizar(estado)
        return True

    def carregar_arquivo(self):
        if self.ocupada:
            return
        caminho = filedialog.askopenfilename(parent=self.root, title="Carregar configuração RM/EDF",
            filetypes=(("Configurações de texto", "*.txt"), ("Todos os arquivos", "*.*")))
        if not caminho:
            return
        try:
            configuracao = self.carregar(Path(caminho))
        except Exception as erro:
            self._erro("Não foi possível carregar o arquivo; a sessão foi preservada", erro)
            return
        if self._chamar("configurar", configuracao, edicao=True):
            self._mensagem(f"Configuração carregada: {caminho}")

    def nova_sessao(self):
        if self.ocupada:
            return
        if self.estado and (self.estado["tarefas"] or self.estado["quadros"]):
            if not messagebox.askyesno("Nova sessão", "Substituir a sessão atual por uma sessão vazia com os padrões RM, 1 CPU e quantum 0?",
                                       parent=self.root):
                return
        self._chamar("configurar", Configuracao(), edicao=True)

    def reiniciar(self):
        if self.ocupada:
            return
        if messagebox.askyesno("Reiniciar sessão", "Restaurar a configuração inicial e descartar a execução e as edições atuais?",
                              parent=self.root):
            self._chamar("reiniciar", edicao=True)

    def avancar(self):
        return self._chamar("avancar")

    def voltar(self):
        return self._chamar("voltar")

    def aplicar_parametros(self):
        if self.ocupada:
            return
        try:
            cpus = inteiro(self.cpus.get(), "CPUs", 1)
            quantum = inteiro(self.quantum.get(), "Quantum")
        except ValueError as erro:
            self._erro("Parâmetros inválidos", erro)
            return
        if self._chamar("alterar_parametros", algoritmo=self.algoritmo.get(), cpus=cpus, quantum=quantum, edicao=True):
            self._mensagem("Parâmetros aplicados. O quantum é armazenado, sem rodízio RM/EDF.")

    def criar_tarefa(self):
        self._abrir_editor(None)

    def editar_tarefa(self):
        tarefa = self.tarefa_selecionada()
        if tarefa is not None:
            self._abrir_editor(tarefa)

    def _abrir_editor(self, tarefa):
        if self.ocupada or self.estado is None:
            return
        if self._dialogo is not None and self._dialogo.winfo_exists():
            self._dialogo.lift()
            return
        self._dialogo = DialogoTarefa(self, tarefa)

    def alterar_estado(self, estado, cpu=None):
        if self.ocupada:
            return False
        tarefa = self.tarefa_selecionada()
        if tarefa is None:
            self._mensagem("Selecione uma tarefa no TCB antes de alterar seu estado.")
            return False
        return self._chamar("alterar_estado", tarefa["chave"], estado, cpu=cpu, edicao=True)

    def executar_na_cpu(self):
        if self.ocupada:
            return
        try:
            cpu = inteiro(self.cpu_manual.get(), "CPU para execução", 1)
        except ValueError as erro:
            self._erro("CPU inválida", erro)
            return
        self.alterar_estado("executando", cpu)

    def executar_completo(self):
        if self.ocupada or self.estado is None:
            return
        self.ocupada = True
        self._habilitar()
        self.resumo.set("Executando até a conclusão ou interrupção… Os comandos serão liberados ao terminar.")
        self.progresso.start(15)

        def trabalhar():
            # Esta thread é a única a acessar a API enquanto ocupada=True.
            # Nem mesmo root.after deve ser chamado fora da thread do Tk.
            try:
                self._fila.put((self.api.executar_completo(), None))
            except Exception as erro:
                self._fila.put((None, erro))

        try:
            Thread(target=trabalhar, name="simulacao-completa", daemon=True).start()
        except Exception as erro:
            self.ocupada = False
            self.progresso.stop()
            self._habilitar()
            self._erro("Não foi possível iniciar a execução completa", erro)
            return
        self._consulta_pendente = self.root.after(50, self._receber_completo)

    def _receber_completo(self):
        self._consulta_pendente = None
        try:
            estado, erro = self._fila.get_nowait()
        except Empty:
            self._consulta_pendente = self.root.after(50, self._receber_completo)
            return
        self.ocupada = False
        self.progresso.stop()
        if erro is not None:
            # A API pode ter avançado antes de uma falha de recursos. Recuperamos
            # o retrato disponível, sem reiniciar ou descartar o modelo.
            try:
                estado = self.api.consultar_estado()
            except Exception as consulta:
                self._mensagem(f"Falha ao consultar o resultado: {consulta}")
            if estado is not None:
                self._atualizar(estado, "Execução interrompida por erro; retrato disponível recuperado.", exportar=False)
            self._erro("Falha na execução completa", erro)
        else:
            self._atualizar(estado, "Execução completa retornou o resultado final.")
        self._habilitar()
        if self._fechar_ao_terminar:
            self.root.destroy()

    def escolher_pasta(self):
        if self.ocupada:
            return
        pasta = filedialog.askdirectory(parent=self.root, title="Pasta para exportação automática",
                                       initialdir=str(self.pasta_saida if self.pasta_saida.exists() else Path.cwd()),
                                       mustexist=False)
        if pasta:
            self.pasta_saida = Path(pasta)
            self.destino.set(str(self.pasta_saida))
            self._mensagem(f"Destino escolhido: {pasta}. A pasta será criada ao exportar.")

    def _nome_exportacao(self):
        tipo = tipo_resultado(self.estado)
        momento = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        return f"gantt-{self.estado['algoritmo'].lower()}-{tipo}-t{self.estado['tempo']}-{momento}.svg"

    def _salvar(self, caminho):
        resultado = Path(self.salvar_svg(self.estado, caminho))
        if not resultado.is_file() or resultado.stat().st_size == 0:
            raise OSError("O exportador não confirmou um arquivo SVG gravado e não vazio.")
        descricao = {"concluida": "concluído", "parcial": "parcial",
                     "encerrada-manualmente": "com término manual"}[tipo_resultado(self.estado)]
        self._mensagem(f"SVG {descricao} salvo: {resultado}")
        return resultado

    def exportar_automaticamente(self):
        if self.ocupada or self.estado is None:
            return False
        caminho = None
        reservado = False
        try:
            self.pasta_saida.mkdir(parents=True, exist_ok=True)
            nome = Path(self._nome_exportacao())
            caminho = self.pasta_saida / nome
            indice = 1
            # Reserva exclusiva: até duas janelas exportando juntas não sobrescrevem.
            while True:
                try:
                    with caminho.open("xb"):
                        pass
                    reservado = True
                    break
                except FileExistsError:
                    caminho = self.pasta_saida / f"{nome.stem}-{indice}{nome.suffix}"
                    indice += 1
            self._salvar(caminho)
            return True
        except Exception as erro:
            if reservado:
                try:
                    if caminho.stat().st_size == 0:
                        caminho.unlink()
                except OSError:
                    pass
            self._erro("Exportação automática falhou; o resultado continua disponível. Escolha outra pasta ou use Salvar SVG como", erro)
            return False

    def exportar_como(self):
        if self.ocupada or self.estado is None:
            return
        caminho = filedialog.asksaveasfilename(parent=self.root, title="Salvar o histórico visível como SVG",
            initialdir=str(self.pasta_saida if self.pasta_saida.exists() else Path.cwd()),
            initialfile=self._nome_exportacao(), defaultextension=".svg", confirmoverwrite=True,
            filetypes=(("Imagem vetorial SVG", "*.svg"),))
        if caminho:
            try:
                destino = Path(caminho)
                destino.parent.mkdir(parents=True, exist_ok=True)
                self._salvar(destino)
            except Exception as erro:
                self._erro("Não foi possível salvar o SVG; a simulação foi preservada", erro)

    def fechar(self):
        if self.ocupada:
            self._fechar_ao_terminar = True
            self._mensagem("A janela fechará após o término da execução completa e da exportação aplicável.")
        else:
            self.root.destroy()

    def _ao_destruir(self, event):
        if event.widget == self.root and self._consulta_pendente is not None:
            self.root.after_cancel(self._consulta_pendente)
            self._consulta_pendente = None


class DialogoTarefa(tk.Toplevel):
    """Editor de todos os campos da definição; progresso vem somente da API."""

    def __init__(self, aplicacao, tarefa):
        super().__init__(aplicacao.root)
        self.aplicacao = aplicacao
        self.tarefa = tarefa
        self.title("Editar tarefa" if tarefa else "Criar tarefa")
        self.transient(aplicacao.root)
        self.resizable(False, False)
        self.campos = {}
        corpo = ttk.Frame(self, padding=16)
        corpo.pack(fill="both", expand=True)
        if tarefa is None:
            dados = asdict(DefinicaoTarefa())
            ids = {t["id"].casefold() for t in aplicacao.estado["tarefas"]}
            numero = 1
            while f"t{numero}" in ids:
                numero += 1
            dados["id"] = f"T{numero}"
            cores = {t["cor"].lstrip("#").upper() for t in aplicacao.estado["tarefas"]}
            cor = next((c for c in CORES if c not in cores), None)
            if cor is None:
                numero_cor = 1
                while f"{numero_cor:06X}" in cores:
                    numero_cor += 1
                cor = f"{numero_cor:06X}"
            dados.update(cor=cor, ingresso=aplicacao.estado["tempo"])
        else:
            dados = tarefa
        rotulos = (("id", "Identificador", "Texto livre; deve ser único."),
                   ("cor", "Cor RGB", "Seis dígitos hexadecimais; cor distinta."),
                   ("ingresso", "Ingresso", "Tick de chegada; não reescreve uma chegada passada."),
                   ("duracao", "Duração", "Ticks necessários por instância; zero é permitido."),
                   ("periodo", "Período", "Intervalo de ativações; zero = aperiódica, ignorada com aviso."),
                   ("prazo", "Prazo relativo", "Deadline = ativação + prazo; pode diferir do período."),
                   ("eventos", "Lista de eventos", "Preservada como texto; não executada no Projeto A."))
        for linha, (chave, titulo, ajuda) in enumerate(rotulos):
            ttk.Label(corpo, text=titulo + ":").grid(row=linha, column=0, sticky="w", pady=5)
            variavel = tk.StringVar(self, texto(dados[chave]))
            self.campos[chave] = variavel
            entrada = ttk.Entry(corpo, textvariable=variavel, width=25)
            entrada.grid(row=linha, column=1, sticky="ew", padx=10)
            ttk.Label(corpo, text=ajuda, wraplength=330).grid(row=linha, column=2, sticky="w")
            if linha == 0:
                entrada.focus_set()
        self.amostra = tk.Label(corpo, text="Prévia da cor", width=16)
        self.amostra.grid(row=7, column=1, padx=10, pady=7, sticky="w")
        ttk.Button(corpo, text="Escolher cor…", command=self.escolher_cor).grid(row=7, column=2, sticky="w")
        self.campos["cor"].trace_add("write", self._cor)
        self._cor()
        explicacao = ("As instâncias liberadas preservam duração, deadline e período/prioridade RM. Alterar o período afeta ativações futuras. "
                      "A API valida as alterações e informa sua abrangência." if tarefa else
                      "Padrões: ingresso no tempo atual, duração 1, período 5 e prazo 5. "
                      "O experimento considera dez conclusões por tarefa periódica.")
        ttk.Label(corpo, text=explicacao, wraplength=610).grid(row=8, column=0, columnspan=3, sticky="w", pady=10)
        botoes = ttk.Frame(corpo)
        botoes.grid(row=9, column=0, columnspan=3, sticky="e")
        ttk.Button(botoes, text="Cancelar", command=self.destroy).pack(side="left", padx=8)
        ttk.Button(botoes, text="Salvar tarefa", command=self.salvar).pack(side="left")
        self.bind("<Escape>", lambda event: self.destroy())
        self.grab_set()

    def _cor(self, *args):
        valor = self.campos["cor"].get().strip().lstrip("#")
        if re.fullmatch(r"[0-9a-fA-F]{6}", valor):
            self.amostra.configure(background=cor_tk(valor), foreground=cor_texto(valor), text="#" + valor.upper())
        else:
            self.amostra.configure(background="white", foreground="#b91c1c", text="RGB inválido")

    def escolher_cor(self):
        valor = self.campos["cor"].get().strip().lstrip("#")
        inicial = cor_tk(valor) if re.fullmatch(r"[0-9a-fA-F]{6}", valor) else "#2563EB"
        _, cor = colorchooser.askcolor(color=inicial, parent=self, title="Cor da tarefa")
        if cor:
            self.campos["cor"].set(cor.lstrip("#").upper())

    def salvar(self):
        dados = {chave: valor.get() for chave, valor in self.campos.items()}
        try:
            for chave, titulo in (("ingresso", "Ingresso"), ("duracao", "Duração"),
                                  ("periodo", "Período"), ("prazo", "Prazo relativo")):
                dados[chave] = inteiro(dados[chave], titulo)
            dados["id"] = dados["id"].strip()
            dados["cor"] = dados["cor"].strip().lstrip("#").upper()
            if not dados["id"]:
                raise ValueError("Identificador: informe um texto não vazio.")
            if not re.fullmatch(r"[0-9A-F]{6}", dados["cor"]):
                raise ValueError("Cor RGB: use seis dígitos hexadecimais, como 2563EB.")
        except ValueError as erro:
            self.aplicacao._erro("Definição inválida", erro)
            return
        if self.tarefa is None:
            sucesso = self.aplicacao._chamar("adicionar_tarefa", DefinicaoTarefa(**dados), edicao=True)
        else:
            sucesso = self.aplicacao._chamar("editar_tarefa", self.tarefa["chave"], edicao=True, **dados)
        if sucesso:
            self.destroy()
