"""Cópia no Google Drive: envio em partes, pasta própria, limpeza das antigas e marca de enviada (Drive simulado)."""
import gzip
import os
import tempfile

pasta = tempfile.mkdtemp()
os.environ.update(CP_BANCO=os.path.join(pasta, "t.db"), CP_SECRET="x", CP_COPIAS_AUTO="0")
import copia_drive  # noqa: E402

falhas = []


class Resp:
    def __init__(self, cod, js=None, headers=None):
        self.status_code, self._js, self.headers = cod, js or {}, headers or {}

    def json(self):
        return self._js

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(self.status_code)


class Drive:
    def __init__(self):
        self.pastas, self.arquivos, self.recebido, self.apagados = [], [], b"", []

    def get(self, url, params=None, timeout=None):
        if "folder" in params["q"]:
            return Resp(200, {"files": [{"id": p} for p in self.pastas]})
        return Resp(200, {"files": sorted(self.arquivos, key=lambda f: f["name"], reverse=True)})

    def post(self, url, params=None, json=None, headers=None, timeout=None):
        if url == copia_drive.API:
            self.pastas.append("P1")
            return Resp(200, {"id": "P1"})
        if json["parents"] != ["P1"] or "drive.file" not in copia_drive.ESCOPO:
            falhas.append("arquivo fora da pasta própria")
        self.arquivos.append({"id": json["name"], "name": json["name"]})
        return Resp(200, headers={"Location": "https://upload/sessao"})

    def put(self, url, data=None, headers=None, timeout=None):
        self.recebido += data
        total = int(headers["Content-Range"].split("/")[1])
        return Resp(200 if len(self.recebido) >= total else 308)

    def delete(self, url, timeout=None):
        self.apagados.append(url.rsplit("/", 1)[1])
        return Resp(204)


d = Drive()
copia_drive._sessao = lambda: d
copia_drive.PEDACO = 1000
for i in range(31):
    d.arquivos.append({"id": f"custoplano-2026-10-{i:02d}.db.gz", "name": f"custoplano-2026-10-{i:02d}.db.gz"})
arq = os.path.join(pasta, "custoplano-2026-11-11-0300.db.gz")
dados = gzip.compress(os.urandom(5000))
open(arq, "wb").write(dados)
copia_drive.enviar(arq)
if d.recebido != dados:
    falhas.append("o arquivo chegou diferente")
if d.pastas != ["P1"]:
    falhas.append("pasta não foi criada uma vez")
if sorted(d.apagados) != ["custoplano-2026-10-00.db.gz", "custoplano-2026-10-01.db.gz"]:
    falhas.append(f"limpeza errada: {d.apagados}")
copia_drive.enviar(arq)
if d.pastas != ["P1"]:
    falhas.append("pasta duplicada no segundo envio")

# sem as variáveis, não liga
for k in ("CP_DRIVE_CHAVE", "CP_DRIVE_USUARIO"):
    os.environ.pop(k, None)
if copia_drive.configurado():
    falhas.append("ligou sem configuração")

# no app: envia a mais recente uma vez e não reenvia
os.environ.update(CP_DRIVE_CHAVE="{}", CP_DRIVE_USUARIO="contato@exemplo.com")
import app  # noqa: E402
import banco  # noqa: E402

banco.copia_de_seguranca()
enviados = []
copia_drive.enviar = lambda c: enviados.append(os.path.basename(c))
app._copia_no_drive()
app._copia_no_drive()
if len(enviados) != 1:
    falhas.append(f"envios pelo app: {enviados}")

if falhas:
    print("FALHOU:\n- " + "\n- ".join(falhas))
    raise SystemExit(1)
print("cópia no Google Drive ok")
