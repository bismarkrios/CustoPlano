"""Registros de acesso (Marco Civil, art. 15): cada login guarda data, hora e IP; com mais de 6 meses sai.
Rodar com:  CP_BANCO=/tmp/acesso.db CP_SECRET=x CP_COPIAS_AUTO=0 python teste_registros_acesso.py
"""
import datetime as dt

import app
import banco

uid = banco.criar_usuario("ana@teste.com", "senha-forte-123", "Ana")
banco.definir_acesso(uid, banco.criar_obra("Obra da Ana"), "editor")
c = app.app.test_client()
r = c.post("/api/login", json={"email": "ana@teste.com", "senha": "senha-forte-123"},
           headers={"X-Forwarded-For": "1.2.3.4, 200.100.50.25"})
assert r.status_code == 200, r.status_code
assert c.post("/api/login", json={"email": "ana@teste.com", "senha": "errada"}).status_code == 401

with banco.conectar() as con:
    regs = con.execute("SELECT usuario_id, email, ip, quando FROM registros_acesso").fetchall()
assert len(regs) == 1, regs  # senha errada não conta como acesso
assert regs[0][0] == uid and regs[0][1] == "ana@teste.com"
assert regs[0][2] == "200.100.50.25", regs[0][2]  # o IP que o Render anota, não o que o navegador inventa
assert dt.datetime.fromisoformat(regs[0][3]) > dt.datetime.now() - dt.timedelta(minutes=5)

# registro antigo (7 meses) é apagado no próximo login; o de 5 meses fica
velho = (dt.datetime.now() - dt.timedelta(days=210)).isoformat(timespec="seconds")
recente = (dt.datetime.now() - dt.timedelta(days=150)).isoformat(timespec="seconds")
with banco.conectar() as con:
    con.execute("INSERT INTO registros_acesso (usuario_id, email, ip, quando) VALUES (?, 'x', '9.9.9.9', ?)", (uid, velho))
    con.execute("INSERT INTO registros_acesso (usuario_id, email, ip, quando) VALUES (?, 'x', '8.8.8.8', ?)", (uid, recente))
assert c.post("/api/login", json={"email": "ana@teste.com", "senha": "senha-forte-123"}).status_code == 200
with banco.conectar() as con:
    ips = sorted(r[0] for r in con.execute("SELECT ip FROM registros_acesso"))
assert "9.9.9.9" not in ips and "8.8.8.8" in ips and len(ips) == 3, ips

# conta removida: o registro fica até completar os 6 meses
banco.remover_usuario(uid)
with banco.conectar() as con:
    assert con.execute("SELECT COUNT(*) FROM registros_acesso").fetchone()[0] == 3
print("Registros de acesso: OK")
