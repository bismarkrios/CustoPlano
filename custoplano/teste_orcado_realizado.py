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
for ruim, msg in ((b"nada", "aceitou arquivo que não é xlsx"),):
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

# obra de demonstração: só a planilha (custo não sai do Project), igual à planilha de exemplo da Engenharia
do = banco.criar_obra("Demo teste", "x")
demo.semear(do)
a = app.analise_custos(do)
C = a and a["controle"]
checar(a and a.get("so_controle"), "a demonstração ainda usa orçamento tirado do cronograma")
checar(C and C["total"]["oin"] == 7888000 and C["total"]["incorrido"] == 5056766, "demonstração fora da planilha de exemplo")
checar(C and C["total"]["estimativa"] == 8120760 and C["total"]["desvio"] == -192760, "estimativa da demonstração errada")
checar(C and all(y["contratado"] == y["incorrido"] + y["saldo_contrato"] + y["saldo_pedido"] for y in C["linhas"]),
       "na demonstração o contratado não fecha com incorrido + saldos")
r = app.gerar_relatorio(do, "custos")
checar(r and r[0][:4] == b"%PDF", "relatório de custos só com a planilha não saiu")
r = app.gerar_relatorio(do, "consolidado")
checar(r and r[0][:4] == b"%PDF", "consolidado só com a planilha não saiu")

# custo nunca sai do MS Project: cronograma com custos e sem planilha não mostra custo
oc = adm.post("/api/admin/obra", json={"nome": "Obra com custo no Project"}).get_json()["id"]
assert adm.post("/api/obra/abrir", json={"id": oc}).status_code == 200
import demo as _d
from cronograma import Projeto  # noqa: E402
xml_custo = _d._xml(_d._tarefas(__import__("datetime").date.today()), __import__("datetime").date.today())
assert adm.post("/api/importar", data={"arquivo": (io.BytesIO(xml_custo.encode()), "obra.xml")}, content_type="multipart/form-data").status_code == 200
checar(adm.get("/api/custos").get_json()["custos"] is None, "custos tirados das colunas de custo do Project")

# outros formatos: CSV com ; e números brasileiros, e Excel com uma aba só de outro nome
csv_ = ("CÓDIGO;SERVIÇO;ORÇAMENTO INICIAL (a);ORÇAMENTO ATUALIZADO (b);CONTRATADO (c);INCORRIDO (d);SALDO DE CONTRATO (e);"
        "SALDO DE PEDIDO (f);SALDO A CONTRATAR (h)\n1.1;Canteiro;100.000;100.000;98.000;98.000;-;-;-\n"
        "2.1;Estrutura;500.000;520.000;560.000;300.000;240.000;20.000;0\n").encode("cp1252")
x = cst.ler_controle("controle.csv", csv_)
checar(len(x["linhas"]) == 2 and x["linhas"][1]["oatu"] == 520000 and x["linhas"][0]["saldo_contrato"] == 0 and not x["avisos"],
       f"CSV lido errado: {x}")
x = cst.ler_controle("qualquer.xlsx", planilha(LINHAS, "Plan1"))
checar(len(x["linhas"]) == 3, "Excel com uma aba só não foi lido")
try:
    cst.ler_controle("antigo.xls", b"x")
    falhas.append("aceitou .xls")
except cst.ErroCustos as e:
    checar("xlsx" in str(e), "mensagem do .xls não orienta salvar como .xlsx")

# arquivo exportado do ERP da construtora (Orçado x Realizado por UC, etapa, item e credor)
wb = openpyxl.Workbook()
ws = wb.active
ws.title = "Orçado x Realizado"
cab = ["Orçado Inicial", "Contratado", "Saldo de Pedido", "Saldo de Contrato", "Ajustes", "Orçado Tendência",
       "Saldo de Orçamento", "Realizado", "A Pagar", "Apropriado financeiro", "% comprometido"]
ws.append(["Orçado x Realizado"])
ws.append([None, "RESUMO EXECUTIVO"] + cab)
ws.append([None, "Total", 1000, 900, 10, 200, 0, 900, 100, 600, 50, 650, 0.9])
ws.append(["Ref.", "Descrição / Credor"] + cab + ["●", "Justificativa"])
ws.append([None, "2 - CUSTOS INDIRETOS", 1000, 900, 10, 200, 0, 900, 100, 600, 50, 650, 0.9])
ws.append(["01", "SERVIÇOS TÉCNICOS", 400, 500, 10, 100, 0, 500, -100, 350, 50, 400, 1.25])
ws.append(["01.000.000.001", "Projetos", 400, 500, 10, 100, 0, 500, -100, 350, 50, 400, 1.25])
ws.append(["CTPS/1", "Fornecedor X", None, 500, None, 100, None, None, None, 350, None, 350])
ws.append(["02", "CANTEIRO", 600, 400, 0, 100, 50, 400, 200, 250, 0, 250, 0.67])
ws.append(["02.000.000.001", "Barracão", 600, 400, 0, 100, 50, 400, 200, 250, 0, 250, 0.67])
out = io.BytesIO()
wb.save(out)
x = cst.ler_controle("erp.xlsx", out.getvalue())
checar([y["codigo"] for y in x["linhas"]] == ["2.01", "2.02"], f"ERP: etapas lidas erradas: {x['linhas']}")
e1, e2 = x["linhas"]
checar(e1["incorrido"] == 400 and e1["saldo_pedido"] == 10 and e1["saldo_contrato"] == 100 and e1["a_contratar"] == 0,
       f"ERP: colunas da etapa 1 erradas: {e1}")
checar(e2["oatu"] == 650 and e2["a_contratar"] == 250, f"ERP: ajustes ou saldo a contratar (b − c) errados: {e2}")
checar(any("premissa" in a_ for a_ in x["avisos"]), "ERP: não avisou que o saldo a contratar é premissa")

if falhas:
    print("FALHAS:\n- " + "\n- ".join(falhas))
    sys.exit(1)
print("orçado x realizado OK")
