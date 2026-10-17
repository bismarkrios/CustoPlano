"""Resumo de engenharia de cada obra de cliente (PPC, medição, NCs), escrito uma vez por dia no log do
servidor. A conferência semanal da equipe lê essa linha nos logs do Render, sem precisar de login.
Só números e o nome da obra: nenhum e-mail, senha ou dado pessoal vai para o log."""
import datetime as dt
import json

import banco

MARCA = "RESUMO-ENGENHARIA"
DIAS_MEDICAO = 7  # data de status mais antiga que isso = medição atrasada


def _ppc(semanas: dict, hoje: dt.date) -> dict | None:
    """PPC da última semana já começada que tem apontamento: concluídos / apontados."""
    for sem in sorted(semanas, reverse=True):
        if sem > hoje.isoformat():
            continue
        ap = semanas[sem].values()
        ok = sum(1 for a in ap if a.get("st") == "ok")
        nok = sum(1 for a in ap if a.get("st") == "nok")
        if ok + nok:
            causas: dict[str, int] = {}
            for a in ap:
                if a.get("st") == "nok" and a.get("causa"):
                    causas[a["causa"]] = causas.get(a["causa"], 0) + 1
            return {"semana": sem, "ppc": round(100 * ok / (ok + nok)), "ok": ok, "nok": nok, "causas": causas}
    return None


def resumo_obras(hoje: dt.date | None = None) -> list[dict]:
    hoje = hoje or dt.date.today()
    with banco.conectar() as con:
        obras = con.execute(
            "SELECT id, nome, empresa FROM obras WHERE id NOT IN ("
            " SELECT a.obra_id FROM acessos a JOIN usuarios u ON u.id = a.usuario_id"
            " WHERE u.email = 'demo@custoplano.com.br') ORDER BY id").fetchall()
        saida = []
        for o in obras:
            r = {"obra": o["nome"], "empresa": o["empresa"]}
            c = con.execute("SELECT data_status FROM cronogramas WHERE usuario_id=?", (o["id"],)).fetchone()
            if c:
                atraso = (hoje - dt.date.fromisoformat(c["data_status"][:10])).days
                r["data_status"] = c["data_status"][:10]
                r["medicao_atrasada_dias"] = atraso if atraso > DIAS_MEDICAO else 0
            else:
                r["cronograma"] = "não importado"
            m = con.execute("SELECT data_status, avanco_real, avanco_prev FROM medicoes_fechadas WHERE usuario_id=? "
                            "ORDER BY id DESC LIMIT 1", (o["id"],)).fetchone()
            if m:
                r["ultima_medicao"] = {"data": m["data_status"][:10], "real": round(m["avanco_real"], 1),
                                       "previsto": round(m["avanco_prev"], 1)}
            cp = con.execute("SELECT dados FROM curto_prazo WHERE usuario_id=?", (o["id"],)).fetchone()
            r["ppc"] = _ppc(json.loads(cp["dados"]) if cp else {}, hoje)
            ncs = con.execute("SELECT status, prazo, gravidade FROM ncs WHERE usuario_id=? AND status != 'fechada'",
                              (o["id"],)).fetchall()
            r["ncs_abertas"] = len(ncs)
            r["ncs_vencidas"] = sum(1 for n in ncs if n["prazo"] and n["prazo"][:10] < hoje.isoformat())
            r["ncs_altas"] = sum(1 for n in ncs if n["gravidade"] == "alta")
            saida.append(r)
    return saida


def registrar_no_log(hoje: dt.date | None = None) -> None:
    hoje = hoje or dt.date.today()
    print(f"{MARCA} {json.dumps({'data': hoje.isoformat(), 'obras': resumo_obras(hoje)}, ensure_ascii=False)}", flush=True)
