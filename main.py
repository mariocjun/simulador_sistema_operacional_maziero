"""Montagem da aplicação. Só esta entrada escolhe entre interface e modo sem tela."""

import argparse
import json
from pathlib import Path
import sys

from api import SimuladorAPI
from configuracao import carregar
from contratos import ErroSimulacao
from exportacao_svg import gerar_svg, salvar_svg


def main(argv=None):
    """Uma entrada reproduzível também permite verificar o executável empacotado."""
    parser = argparse.ArgumentParser(description="Simulador didático de escalonamento RM/EDF")
    parser.add_argument("--sem-interface", metavar="CONFIG", help="executa o arquivo até o fim, sem abrir janela")
    parser.add_argument("--arquivo", help="abre a janela com uma configuração escolhida explicitamente")
    parser.add_argument("--saida", help="SVG de destino explícito; padrão: gantt.svg, sem sobrescrever")
    parser.add_argument("--relatorio", help="grava resultado e diagnóstico de execução em JSON")
    parser.add_argument("--semente", type=int, default=0, help="semente do sorteio (padrão: 0, reproduzível)")
    args = parser.parse_args(argv)
    try:
        api = SimuladorAPI(semente=args.semente)
        if args.sem_interface or args.arquivo:
            api.configurar(carregar(args.sem_interface or args.arquivo))
        if args.sem_interface:
            estado = api.executar_completo()
            if args.saida is not None:
                destino = salvar_svg(estado, args.saida)
            else:
                # Um destino automático nunca substitui um resultado anterior.
                documento = gerar_svg(estado)
                destino = Path("gantt.svg")
                with destino.open("x", encoding="utf-8") as arquivo:
                    arquivo.write(documento)
            relatorio = {
                "situacao": estado["situacao"], "tempo": estado["tempo"],
                "tarefas": [{"id": t["id"], "concluidas": t["concluidas"]} for t in estado["tarefas"]],
                "perdas_de_prazo": sum(e["tipo"] == "prazo" for e in estado["eventos"]),
                "svg": str(destino.resolve()), "avisos": estado["avisos"],
                "executavel": sys.executable, "python": sys.version.split()[0],
                "empacotado": bool(getattr(sys, "frozen", False)),
            }
            if args.relatorio:
                Path(args.relatorio).write_text(json.dumps(relatorio, ensure_ascii=False, indent=2), encoding="utf-8")
            if sys.stdout is not None:
                # JSON ASCII também funciona em consoles Windows sem UTF-8.
                print(json.dumps(relatorio, ensure_ascii=True))
            return 0 if estado["situacao"] == "concluida" else 2

        # Imports gráficos ficam na entrada, não na API reutilizável.
        import tkinter as tk
        from interface_tk import Aplicacao
        root = tk.Tk()
        Aplicacao(root, api, carregar, salvar_svg, configuracao_carregada=bool(args.arquivo))
        root.mainloop()
        return 0
    except (ErroSimulacao, OSError, ValueError) as erro:
        if args.relatorio:
            try:
                Path(args.relatorio).write_text(json.dumps({"erro": str(erro)}, ensure_ascii=False), encoding="utf-8")
            except OSError:
                pass  # O erro original continua sendo comunicado abaixo.
        if sys.stderr is not None:
            print(f"Erro: {erro}", file=sys.stderr)
        if not args.sem_interface:
            from tkinter import messagebox
            messagebox.showerror("Não foi possível iniciar", str(erro))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
