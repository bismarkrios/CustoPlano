"""Teste do resumo de engenharia que vai para o log (PPC, medição atrasada, NCs abertas, qualidade).
Rodar com:  CP_BANCO=/tmp/resumo.db CP_SECRET=x CP_COPIAS_AUTO=0 python teste_resumo.py
"""
import datetime as dt
import io
import json
import sys
from contextlib import redirect_stdout

import app  # noqa: F401 - cria o banco e a obra de demonstração
import banco
import resumo

falhas = []


def checar(cond, msg):
    if not cond:
        falhas.append(msg)


hoje = dt.date(2026, 10, 19)
o = banco.criar_obra("Residencial Teste", "Cliente X")
banco.salvar_cronograma(o, "obra.xml", "<Project/>", "dur", "2026-10-05", {})
banco.salvar_curto_prazo(o, {
    "2026-10-05": {"1": {"st": "ok", "causa": ""}},
    "2026-10-12": {"1": {"st": "ok", "causa": ""}, "2": {"st": "ok", "causa": ""}, "3": {"st": "nok", "causa": "Material"},
                   "4": {"st": "ok", "causa": ""}},
    "2026-10-26": {"1": {"st": "nok", "causa": "Clima"}},  # semana futura não conta
})
with banco.conectar() as con:
    for n, (st, prazo, grav) in enumerate([("aberta", "2026-10-10", "alta"), ("tratamento", "2026-11-30", "media"),
                                           ("fechada", "2026-10-01", "alta")], 1):
        con.execute("INSERT INTO ncs (usuario_id, numero, origem, descricao, gravidade, prazo, status, criado_em, atualizado_em) "
                    "VALUES (?, ?, 'Obra', 'x', ?, ?, ?, '2026-10-01', '2026-10-01')", (o, n, grav, prazo, st))
    con.execute("UPDATE ncs SET servico='Pintura', responsavel='Mestre' WHERE usuario_id=? AND numero=2", (o,))
    con.execute("UPDATE ncs SET fechada_em='2026-10-05' WHERE usuario_id=? AND numero=3", (o,))
    for data, res, reinsp in [("2026-10-01", "reprovada", None), ("2026-10-08", "aprovada", 1), ("2026-10-10", "aprovada", None),
                              ("2026-08-01", "aprovada", None)]:  # a de agosto fica fora dos 30 dias
        con.execute("INSERT INTO inspecoes (usuario_id, codigo, servico, local, data, resultado, itens, reinspecao_de, criado_em) "
                    "VALUES (?, 'FVS-01', 'Alvenaria', '1º pav', ?, ?, '[]', ?, ?)", (o, data, res, reinsp, data))

r = resumo.resumo_obras(hoje)
nomes = [x["obra"] for x in r]
checar("Obra de demonstração" not in nomes, f"a demo entrou no resumo: {nomes}")
x = next((x for x in r if x["obra"] == "Residencial Teste"), None)
checar(x is not None, "a obra não apareceu")
if x:
    checar(x["ppc"] and x["ppc"]["semana"] == "2026-10-12" and x["ppc"]["ppc"] == 75, f"PPC errado: {x['ppc']}")
    checar(x["ppc"] and x["ppc"]["causas"] == {"Material": 1}, f"causas erradas: {x['ppc']}")
    checar(x["medicao_atrasada_dias"] == 14, f"atraso de medição errado: {x}")
    checar(x["ncs_abertas"] == 2 and x["ncs_vencidas"] == 1 and x["ncs_altas"] == 1, f"NCs erradas: {x}")
    q = x["qualidade"]
    checar(q["inspecoes_30d"] == 3 and q["reinspecoes_30d"] == 1 and q["aprovacao_1a_pct"] == 50, f"inspeções erradas: {q}")
    checar(q["ncs_abertas_por_servico"] == {"Sem serviço": 1, "Pintura": 1} and q["ncs_sem_responsavel"] == 1, f"NCs abertas erradas: {q}")
    checar(q["ncs_fechadas_30d"] == 1 and q["dias_medio_fechamento"] == 4, f"fechamento errado: {q}")

o2 = banco.criar_obra("Obra vazia")
y = next(x for x in resumo.resumo_obras(hoje) if x["obra"] == "Obra vazia")
checar(y["cronograma"] == "não importado" and y["ppc"] is None and y["ncs_abertas"] == 0, f"obra vazia errada: {y}")
checar(y["qualidade"]["inspecoes_30d"] == 0 and y["qualidade"]["aprovacao_1a_pct"] is None, f"qualidade da obra vazia errada: {y}")

buf = io.StringIO()
with redirect_stdout(buf):
    resumo.registrar_no_log(hoje)
linha = buf.getvalue().strip()
checar(linha.startswith("RESUMO-ENGENHARIA "), f"linha do log errada: {linha[:80]}")
checar(json.loads(linha.split(" ", 1)[1])["data"] == "2026-10-19", "data errada no log")
checar("@" not in linha, "o log tem e-mail")

if falhas:
    print("FALHOU:\n- " + "\n- ".join(falhas))
    sys.exit(1)
print("resumo de engenharia ok")
