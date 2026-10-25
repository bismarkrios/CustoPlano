"""Teste da tabela Orçado x Realizado (estrutura da planilha de controle da Engenharia, 25/10/2026):
a planilha entra pela tela Custos, o sistema calcula (g), (i), (j) e (k), confere o total com o orçamento
e com os custos lançados, aparece no relatório de custos e a obra de demonstração já vem preenchida.
Rodar com:  CP_BANCO=/tmp/ctl.db CP_SECRET=x CP_COPIAS_AUTO=0 python teste_orcado_realizado.py
"""
import io
import os
import sys

import openpyxl

import app
import banco
import custos as cst
import demo

falhas = []


def checar(cond, msg):
    if not cond:
        falhas.append(msg)


def planilha(linhas, aba="Orçado x Realizado"):
    """Mesmo layout do gerador da Engenharia: cabeçalho com (a)...(k) e fórmulas sem valor guardado."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = aba
    ws.append(["Obra", "Teste"])
    ws.append([])
    ws.append(["CÓDIGO", "SERVIÇO", "ORÇAMENTO\nINICIAL\n(a)", "ORÇAMENTO\nATUALIZADO\n(b)", "CONTRATADO\n\n(c)",
               "INCORRIDO\n\n(d)", "SALDO DE\nCONTRATO\n(e)", "SALDO DE\nPEDIDO\n(f)", "INCORRIDO +\nSALDOS\n(g)",
               "SALDO A\nCONTRATAR\n(h)", "ESTIMATIVA\nNO TÉRMINO\n(i)", "DESVIO\n\n(j)", "ÍNDICE\n(i) ÷ (b)\n(k)"])
    for i, (cod, serv, a, b, c, d, e, f, h) in enumerate(linhas):
        r = 4 + i
        ws.append([cod, serv, a, b, c, d, e, f, f"=F{r}+G{r}+H{r}", h, f"=I{r}+J{r}", f"=D{r}-K{r}", f"=K{r}/D{r}"])
    ws.append(["", "TOTAL DA OBRA", "=SUM(C4:C9)"])
    ws.append(["(a)", "Orçamento inicial", "texto de legenda"])
    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()


LINHAS = [("1.1", "Canteiro", 100000, 100000, 98000, 98000, 0, 0, 0),
          ("2.1", "Estrutura", 500000, 520000, 560000, 300000, 240000, 20000, 0),
          ("2.2", "Pintura", 200000, 200000, 0, 0, 0, 0, 190000)]

# leitura e cálculo
x = cst.ler_controle("controle.xlsx", planilha(LINHAS))
checar([y["codigo"] for y in x["linhas"]] == ["1.1", "2.1", "2.2"], f"linhas lidas erradas: {x['linhas']}")
checar(x["linhas"][1]["saldo_pedido"] == 20000 and x["linhas"][2]["a_contratar"] == 190000, "colunas trocadas na leitura")
r = cst.calcular_controle(x)
e = r["linhas"][1]
checar(e["comprometido"] == 560000 and e["estimativa"] == 560000 and e["desvio"] == -40000, f"conta da linha errada: {e}")
checar(abs(e["indice"] - 1.077) < 0.001, f"índice errado: {e['indice']}")
T = r["total"]
checar(T["oin"] == 800000 and T["estimativa"] == 848000 and T["desvio"] == -28000, f"total errado: {T}")
checar(r["avisos"] == [], "avisou sem motivo")
r = cst.calcular_controle(x, orcado_sistema=900000, realizado_sistema=350000)
checar(len(r["avisos"]) == 2, "não avisou que a planilha difere do orçamento e do realizado do sistema")
for ruim, msg in ((b"nada", "aceitou arquivo que não é xlsx"), (planilha(LINHAS, "Plan1"), "aceitou planilha sem a aba")):
    try:
        cst.ler_controle("x.xlsx", ruim)
        falhas.append(msg)
    except cst.ErroCustos:
        pass

# pela tela Custos: importar, ver, leitor não grava, remover
banco.criar_usuario("equipe@teste.com", "senha-forte-123", "Equipe", admin=True)
adm = app.app.test_client()
assert adm.post("/api/login", json={"email": "equipe@teste.com", "senha": "senha-forte-123"}).status_code == 200
oid = adm.post("/api/admin/obra", json={"nome": "Obra controle"}).get_json()["id"]
assert adm.post("/api/obra/abrir", json={"id": oid}).status_code == 200
xml = open(os.path.join(app.AQUI, "exemplo", "cronograma_exemplo.xml"), "rb").read()
assert adm.post("/api/importar", data={"arquivo": (io.BytesIO(xml), "obra.xml")}, content_type="multipart/form-data").status_code == 200
assert adm.post("/api/custos/exemplo").status_code == 200  # a tela Custos precisa de um orçamento
r = adm.post("/api/custos/controle", data={"arquivo": (io.BytesIO(planilha(LINHAS)), "controle.xlsx")}, content_type="multipart/form-data")
checar(r.status_code == 200, f"importar a planilha falhou: {r.get_json()}")
C = (adm.get("/api/custos").get_json()["custos"] or {}).get("controle")
checar(C and C["total"]["estimativa"] == 848000 and C["arquivo"] == "controle.xlsx", "a tela Custos não traz o Orçado x Realizado")
checar(C and any("orçamento inicial" in a for a in C["avisos"]), "não conferiu o orçamento inicial com o do sistema")
r = adm.get("/api/relatorio.pdf?tipo=custos")
checar(r.status_code == 200 and r.data[:4] == b"%PDF", "relatório de custos não saiu com o Orçado x Realizado")
r = adm.post("/api/custos/controle", data={"arquivo": (io.BytesIO(b"x"), "a.txt")}, content_type="multipart/form-data")
checar(r.status_code == 400, "aceitou arquivo errado")
banco.criar_usuario("leitor@teste.com", "senha-forte-123", "Leitor")
banco.definir_acesso(banco.usuario_por_email("leitor@teste.com")["id"], oid, "leitor")
lei = app.app.test_client()
assert lei.post("/api/login", json={"email": "leitor@teste.com", "senha": "senha-forte-123"}).status_code == 200
lei.post("/api/obra/abrir", json={"id": oid})
checar(lei.get("/api/custos").get_json()["custos"]["controle"] is not None, "leitor não vê o Orçado x Realizado")
checar(lei.post("/api/custos/controle/remover").status_code == 403, "leitor conseguiu remover a planilha")
checar(adm.post("/api/custos/controle/remover").status_code == 200, "remover falhou")
checar(adm.get("/api/custos").get_json()["custos"]["controle"] is None, "a planilha continuou depois de removida")
checar(adm.get("/api/custos").get_json()["custos"]["totais"]["bac"] > 0, "remover a planilha apagou o orçamento")

# obra de demonstração: preenchida, coerente com o orçamento e os lançamentos do próprio sistema
do = banco.criar_obra("Demo teste", "x")
demo.semear(do)
a = app.analise_custos(do)
C = a["controle"]
checar(C and C["total"]["oin"] == round(a["totais"]["bac"]), "orçamento inicial da demonstração não bate com o sistema")
checar(C and abs(C["total"]["incorrido"] - a["totais"]["cr"]) <= len(C["linhas"]), "incorrido da demonstração não bate com o realizado")
checar(C and C["avisos"] == [], f"demonstração com aviso: {C and C['avisos']}")
checar(C and all(y["contratado"] == y["incorrido"] + y["saldo_contrato"] + y["saldo_pedido"] for y in C["linhas"]),
       "na demonstração o contratado não fecha com incorrido + saldos")
checar(C and C["total"]["estimativa"] == 8120760 and C["total"]["desvio"] == -192760, "demonstração fora dos números da Engenharia")

if falhas:
    print("FALHAS:\n- " + "\n- ".join(falhas))
    sys.exit(1)
print("orçado x realizado OK")
