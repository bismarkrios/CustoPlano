"""Teste das melhorias pedidas pela Engenharia (25/10/2026):
1. o Custo real do Project não entra como realizado (sem lançamento real: "sem lançamentos reais");
2. o orçamento no padrão Custo Plano (árvore UA, fórmulas sem valor guardado) entra direto e liga pelo código UA;
3. o resumo diário conta as FVS atrasadas do cronograma;
4. o resumo avisa a medição física parada há mais de 7 dias.
Rodar com:  CP_BANCO=/tmp/eng.db CP_SECRET=x CP_COPIAS_AUTO=0 python teste_engenharia.py
"""
import datetime as dt
import io
import sys

import openpyxl

import app  # cria o banco e a obra de demonstração
import banco
import custos as cst
import qualidade
import resumo
from cronograma import Projeto

falhas = []


def checar(cond, msg):
    if not cond:
        falhas.append(msg)


def xml(tarefas, status="2026-10-05"):
    """tarefas: (uid, eap, nível, nome, início, término, custo R$, custo real R$, código UA)"""
    partes = []
    for uid, eap, nivel, nome, ini, fim, custo, real, ua in tarefas:
        c = f"<Cost>{int(custo * 100)}</Cost>" if custo else ""
        r = f"<ActualCost>{int(real * 100)}</ActualCost>" if real else ""
        ea = f"<ExtendedAttribute><FieldID>188743731</FieldID><Value>{ua}</Value></ExtendedAttribute>" if ua else ""
        partes.append(f"<Task><UID>{uid}</UID><ID>{uid}</ID><Name>{nome}</Name><WBS>{eap}</WBS><OutlineLevel>{nivel}</OutlineLevel>"
                      f"<Start>{ini}T08:00:00</Start><Finish>{fim}T17:00:00</Finish><Duration>PT80H0M0S</Duration>"
                      f"<Milestone>0</Milestone><PercentComplete>0</PercentComplete>{c}{r}{ea}"
                      f"<Baseline><Number>0</Number><Start>{ini}T08:00:00</Start><Finish>{fim}T17:00:00</Finish>{c}</Baseline></Task>")
    return ('<?xml version="1.0" encoding="UTF-8"?><Project xmlns="http://schemas.microsoft.com/project">'
            f"<StatusDate>{status}T17:00:00</StatusDate><ExtendedAttributes><ExtendedAttribute><FieldID>188743731</FieldID>"
            "<FieldName>Text1</FieldName><Alias>Código UA</Alias></ExtendedAttribute></ExtendedAttributes>"
            f"<Tasks>{''.join(partes)}</Tasks></Project>")


CRON = xml([
    (1, "1", 1, "Fundações", "2026-09-01", "2026-09-30", 0, 0, ""),
    (2, "1.1", 2, "Estacas", "2026-09-01", "2026-09-15", 6000, 3000, "GSE20"),
    (3, "1.2", 2, "Blocos", "2026-09-16", "2026-09-30", 4000, 0, "GSE20"),
    (4, "2", 1, "Administração", "2026-09-01", "2026-12-31", 2000, 500, "GSA"),
])

# 1. Custo real do Project não é realizado
p = Projeto(CRON, "obra.xml")
d = cst.dados_do_projeto(p)
checar(d and d["realizado"] == [], f"o custo real do Project virou realizado: {d and d['realizado']}")
checar(d and any("Custo real do Project" in a for a in d["avisos"]), f"faltou o aviso do custo real: {d and d['avisos']}")
a = cst.analisar(d, [], p, {"2": 50}, dt.date(2026, 10, 5))
checar(a["sem_realizado"] and a["totais"]["cr"] == 0 and a["totais"]["idc"] is None, f"realizado inventado: {a['totais']}")
a2 = cst.analisar(d, [{"data": "2026-09-10", "codigo": "1.1", "valor": 2500.0, "origem": "sistema"}], p, {"2": 50}, dt.date(2026, 10, 5))
checar(not a2["sem_realizado"] and a2["totais"]["cr"] == 2500, f"lançamento real não contou: {a2['totais']}")
r = resumo._custo(a)
checar(r.get("realizado") == "sem lançamentos reais" and "idc" not in r and "realizado_pct" not in r, f"resumo com realizado suposto: {r}")

# 2. orçamento padrão (Planilha de Orçamento com fórmulas, como o planilha_custoplano.py grava)
wb = openpyxl.Workbook()
pr = wb.active
pr.title = "Premissas"
pr["A5"], pr["B5"] = "Área construída", 100
pr["A6"], pr["B6"] = "INCC", 2
pr["A7"], pr["B7"] = "Contingência", 0.05
ws = wb.create_sheet("Planilha de Orçamento", 0)
ws.append(["PLANILHA DE ORÇAMENTO ANALÍTICA"])
ws.append([])
ws.append(["UA", "CÓDIGO", "NÍVEL", "DESCRIÇÃO", "QTDE", "UNID.", "UNITÁRIO (R$)", "VALOR (R$)", "R$/m²", "%"])
linhas = [
    ("1000 0000", None, "UA-0", "OBRA TESTE", None, None, None, "=H5+H9", None),          # 4
    ("1100 0000", None, "UA-1", "CUSTOS INDIRETOS", None, None, None, "=H6", None),        # 5
    ("1110 0000", None, "UA-2", "ENGENHARIA A MONTANTE", None, None, None, "=H7", None),   # 6
    ("1110 1000", "GSA01", "UA-3", "GSA01  DESPESAS", None, None, None, "=SUM(H8:H8)", None),  # 7
    (None, None, "UA-4", "Despesas (paramétrico)", "='Premissas'!$B$5", "m²", "=ROUND(3.335*'Premissas'!$B$6,2)", "=ROUND(E8*G8,2)", None),  # 8
    ("1200 0000", None, "UA-1", "CUSTOS DIRETOS", None, None, None, "=H10+H15", None),     # 9
    ("1220 0000", None, "UA-2", "INFRAESTRUTURA", None, None, None, "=H11", None),         # 10
    ("1220 2000", "GSE20", "UA-3", "GSE20  FUNDAÇÕES", None, None, None, "=SUM(H12:H13)", None),  # 11
    (None, None, "UA-4", "Estaca hélice contínua", 112, "m", 50, "=ROUND(E12*G12,2)", None),       # 12
    (None, None, "UA-4", "Blocos", 10, "m³", 400, "=ROUND(E13*G13,2)", None),                      # 13
    (None, None, "UA-4", "", None, None, None, None, None),                                        # 14 (vazia)
    ("1290 0000", None, "UA-2", "CONTINGÊNCIAS", None, None, None, "=H16", None),           # 15
    ("1290 1000", "GSE100", "UA-3", "GSE100  CONTINGÊNCIA", None, None, None, "=SUM(H17:H17)", None),  # 16
    (None, None, "UA-4", "Contingência", "='Premissas'!$B$7", "%", "=(H7+H11)", "=ROUND(E17*G17,2)", None),  # 17
]
for ln in linhas:
    ws.append(list(ln))
buf = io.BytesIO()
wb.save(buf)
dp = cst.ler_custos("obra-orcamento-UA-rev00.xlsx", buf.getvalue())
itens = [i for i in dp["itens"] if not i["titulo"]]
checar([i["ua"] for i in itens] == ["GSA01", "GSE20", "GSE20", "GSE100"], f"itens do padrão errados: {[(i['codigo'], i.get('ua')) for i in itens]}")
desp = 100 * 6.67  # ROUND(3.335*2, 2) = 6.67 (metade para cima, como o Excel)
fund = 112 * 50 + 10 * 400
cont = round(0.05 * (desp + fund), 2)
checar([i["total_planilha"] for i in itens] == [desp, 5600, 4000, cont], f"valores errados: {[i['total_planilha'] for i in itens]}")
checar(dp["origem"] == "padrao" and dp["bdi"] == 0 and dp["realizado"] == [], f"cabeçalho do orçamento padrão errado: {dp}")
a = cst.analisar(dp, [], p, {"2": 100, "3": 50}, dt.date(2026, 10, 5))
checar(abs(a["totais"]["bac"] - (desp + fund + cont)) < 0.01, f"total do orçamento errado: {a['totais']['bac']}")
checar([e["nome"] for e in a["etapas"]] == ["ENGENHARIA A MONTANTE", "INFRAESTRUTURA", "CONTINGÊNCIAS"], f"etapas erradas: {[e['nome'] for e in a['etapas']]}")
est = next(i for i in a["itens"] if i["descricao"] == "Estaca hélice contínua")
# GSE20 está em duas tarefas (60% e 40% do custo): agregado = 5600 × (0,6 × 100% + 0,4 × 50%)
checar(abs(est["va"] - 5600 * 0.8) < 0.01 and "(+1 tarefas)" in est["tarefa"], f"GSE20 mal repartido: {est['va']} {est['tarefa']}")
gsa = next(i for i in a["itens"] if i["descricao"].startswith("Despesas"))
checar(gsa["tarefa"].startswith("2 Administração"), f"GSA01 não ligou na tarefa GSA: {gsa['tarefa']}")
checar(a["sem_vinculo"]["n"] == 1, f"só a contingência devia ficar sem tarefa: {a['sem_vinculo']}")

# 3 e 4. FVS atrasadas e medição parada no resumo
hoje = dt.date(2026, 10, 19)
o = banco.criar_obra("Torre Teste")
cron_fvs = xml([
    (1, "1", 1, "Torre A", "2026-08-01", "2026-12-30", 0, 0, ""),
    (2, "1.1", 2, "Alvenaria – 3º pav.", "2026-09-01", "2026-09-20", 0, 0, ""),   # vencida, mas aprovada
    (3, "1.2", 2, "Alvenaria – 4º pav.", "2026-09-21", "2026-10-10", 0, 0, ""),   # vencida e sem inspeção
    (4, "1.3", 2, "Alvenaria – 5º pav.", "2026-10-11", "2026-10-17", 0, 0, ""),   # vence em 19/10: ainda não
    (5, "1.4", 2, "Limpeza final", "2026-09-01", "2026-09-05", 0, 0, ""),          # sem FVS
    (6, "1.5", 2, "Alvenaria – 6º pav.", "2026-09-01", "2026-09-05", 0, 0, ""),   # reprovada: continua atrasada
], status="2026-10-05")
banco.salvar_cronograma(o, "torre.xml", cron_fvs, "dur", "2026-10-05", {})
with banco.conectar() as con:
    mid = con.execute("INSERT INTO fvs_modelos (usuario_id, codigo, servico, itens, criado_em) VALUES (?, 'FVS-01', "
                      "'Alvenaria de vedação', '[]', '2026-10-01')", (o,)).lastrowid
    con.execute("INSERT INTO fvs_modelos (usuario_id, codigo, servico, itens, criado_em) VALUES (?, 'FVS-02', "
                "'Pintura', '[]', '2026-10-01')", (o,))
    for local, res in (("Torre A · 3º pavimento", "aprovada"), ("Torre A · 6º pavimento", "reprovada")):
        con.execute("INSERT INTO inspecoes (usuario_id, codigo, servico, modelo_id, local, data, resultado, itens, criado_em) "
                    "VALUES (?, 'FVS-01', 'Alvenaria', ?, ?, '2026-09-22', ?, '[]', '2026-09-22')", (o, mid, local, res))
n = qualidade.fvs_atrasadas(o, Projeto(cron_fvs, "torre.xml"), hoje)
checar(n == 2, f"FVS atrasadas erradas: {n} (esperado 2: 4º e 6º pavimentos)")
checar(qualidade.fvs_atrasadas(o, None, hoje) is None, "sem cronograma devia ser None")

x = next(x for x in resumo.resumo_obras(hoje, app.analise_custos, app.projeto_da_obra) if x["obra"] == "Torre Teste")
checar(x["fvs_atrasadas"] == 2, f"FVS atrasadas fora do resumo: {x.get('fvs_atrasadas')}")
checar(x["medicao_atrasada_dias"] == 14, f"atraso de medição errado: {x['medicao_atrasada_dias']}")
checar(any("medição física parada há 14 dias" in s for s in x["alertas"]), f"faltou o alerta de medição parada: {x['alertas']}")
checar(any("2 FVS" in s for s in x["alertas"]), f"faltou o alerta de FVS: {x['alertas']}")
banco.salvar_cronograma(o, "torre.xml", cron_fvs, "dur", "2026-10-15", {})
y = next(x for x in resumo.resumo_obras(hoje, None, app.projeto_da_obra) if x["obra"] == "Torre Teste")
checar(not any("medição" in s for s in y["alertas"]), f"alerta de medição com 4 dias: {y['alertas']}")

if falhas:
    print("FALHOU:\n- " + "\n- ".join(falhas))
    sys.exit(1)
print("melhorias da engenharia ok")
