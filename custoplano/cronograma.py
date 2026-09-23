"""
Custo Plano — leitura, medição e exportação de cronogramas do MS Project.

- Lê .mpp direto (via MPXJ, que precisa de Java) ou .xml salvo pelo MS Project.
- Calcula o avanço físico ponderado pelo peso escolhido (campo personalizado
  do Project, ex.: "Peso", ou a duração das tarefas).
- Exporta:
    * .xml que o MS Project abre direto, com % concluído, % físico, datas
      reais e data de status atualizados;
    * boletim de medição em .csv (lido pela macro CustoPlano.bas) e .xlsx.
"""
from __future__ import annotations

import csv
import datetime as dt
import io
import os
import re
import tempfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field

NS = "http://schemas.microsoft.com/project"
ET.register_namespace("", NS)
Q = "{%s}" % NS


class ErroCronograma(Exception):
    """Erro com mensagem pronta para mostrar ao usuário."""


# ----------------------------------------------------------------------------
# Conversão .mpp -> .xml (MPXJ)
# ----------------------------------------------------------------------------
def mpp_para_xml(dados: bytes) -> str:
    """Converte um .mpp em XML do MS Project usando a biblioteca MPXJ.

    Requer: pip install mpxj  (e Java 11 ou mais novo instalado).
    """
    try:
        import mpxj  # noqa: F401  (coloca os .jar do MPXJ no classpath)
        import jpype
        import jpype.imports  # noqa: F401
    except ImportError as e:  # pragma: no cover - depende do ambiente
        raise ErroCronograma(
            "Para ler arquivos .mpp instale o MPXJ: pip install mpxj (é preciso ter o Java 11+ instalado)."
        ) from e

    if not jpype.isJVMStarted():
        jpype.startJVM()
    try:  # MPXJ 14+ usa o pacote org.mpxj; versões antigas, net.sf.mpxj
        from org.mpxj.reader import UniversalProjectReader  # type: ignore
        from org.mpxj.mspdi import MSPDIWriter  # type: ignore
    except ImportError:  # pragma: no cover
        from net.sf.mpxj.reader import UniversalProjectReader  # type: ignore
        from net.sf.mpxj.mspdi import MSPDIWriter  # type: ignore

    with tempfile.TemporaryDirectory() as tmp:
        origem = os.path.join(tmp, "cronograma.mpp")
        destino = os.path.join(tmp, "cronograma.xml")
        with open(origem, "wb") as f:
            f.write(dados)
        projeto = UniversalProjectReader().read(origem)
        if projeto is None:
            raise ErroCronograma("O MPXJ não reconheceu este arquivo como um cronograma do MS Project.")
        MSPDIWriter().write(projeto, destino)
        with open(destino, encoding="utf-8") as f:
            return f.read()


def ler_arquivo(nome: str, dados: bytes) -> str:
    """Recebe o arquivo enviado (.mpp ou .xml) e devolve o XML do projeto."""
    ext = os.path.splitext(nome.lower())[1]
    if ext == ".mpp":
        xml = mpp_para_xml(dados)
    elif ext == ".xml":
        xml = dados.decode("utf-8-sig", errors="replace")
    else:
        raise ErroCronograma("Envie o cronograma em .mpp ou em .xml salvo pelo MS Project.")
    Projeto(xml, nome)  # valida
    return xml


# ----------------------------------------------------------------------------
# Utilidades
# ----------------------------------------------------------------------------
_DUR = re.compile(r"PT(\d+(?:\.\d+)?)H(\d+(?:\.\d+)?)M(\d+(?:\.\d+)?)S")


def horas(txt: str) -> float:
    m = _DUR.match(txt or "")
    return float(m[1]) + float(m[2]) / 60 + float(m[3]) / 3600 if m else 0.0


def pt_dur(h: float) -> str:
    H = int(h)
    M = round((h - H) * 60)
    if M == 60:
        H, M = H + 1, 0
    return f"PT{H}H{M}M0S"


def data(txt: str) -> dt.datetime | None:
    try:
        return dt.datetime.strptime((txt or "")[:16], "%Y-%m-%dT%H:%M")
    except ValueError:
        return None


def br(v: float, casas: int = 1) -> str:
    return f"{v:.{casas}f}".replace(".", ",")


def br_data(d: dt.datetime | None) -> str:
    return d.strftime("%d/%m/%y") if d else "—"


def num(txt: str) -> float | None:
    try:
        return float((txt or "").replace("%", "").replace(",", "."))
    except ValueError:
        return None


def _filho(el: ET.Element, nome: str) -> ET.Element | None:
    return el.find(Q + nome)


def _txt(el: ET.Element, nome: str) -> str:
    k = _filho(el, nome)
    return (k.text or "").strip() if k is not None else ""


# Ordem dos campos da tarefa no XML do MS Project (para inserir no lugar certo)
ORDEM_TAREFA = [
    "UID", "GUID", "ID", "Name", "Active", "Manual", "Type", "IsNull", "CreateDate", "Contact", "WBS", "WBSLevel",
    "OutlineNumber", "OutlineLevel", "Priority", "Start", "Finish", "Duration", "ManualStart", "ManualFinish",
    "ManualDuration", "DurationFormat", "Work", "Stop", "Resume", "ResumeValid", "EffortDriven", "Recurring",
    "OverAllocated", "Estimated", "Milestone", "Summary", "DisplayAsSummary", "Critical", "IsSubproject",
    "IsSubprojectReadOnly", "SubprojectName", "ExternalTask", "ExternalTaskProject", "EarlyStart", "EarlyFinish",
    "LateStart", "LateFinish", "StartVariance", "FinishVariance", "WorkVariance", "FreeSlack", "TotalSlack",
    "StartSlack", "FinishSlack", "FixedCost", "FixedCostAccrual", "PercentComplete", "PercentWorkComplete", "Cost",
    "OvertimeCost", "OvertimeWork", "ActualStart", "ActualFinish", "ActualDuration", "ActualCost",
    "ActualOvertimeCost", "ActualWork", "ActualOvertimeWork", "RegularWork", "RemainingDuration", "RemainingCost",
    "RemainingWork", "RemainingOvertimeCost", "RemainingOvertimeWork", "ACWP", "CV", "ConstraintType",
    "CalendarUID", "ConstraintDate", "Deadline", "LevelAssignments", "LevelingCanSplit", "LevelingDelay",
    "LevelingDelayFormat", "PreLeveledStart", "PreLeveledFinish", "Hyperlink", "HyperlinkAddress",
    "HyperlinkSubAddress", "IgnoreResourceCalendar", "Notes", "HideBar", "Rollup", "BCWS", "BCWP",
    "PhysicalPercentComplete", "EarnedValueMethod", "PredecessorLink", "ActualWorkProtected",
    "ActualOvertimeWorkProtected", "ExtendedAttribute", "Baseline", "OutlineCode", "IsPublished", "StatusManager",
    "CommitmentStart", "CommitmentFinish", "CommitmentType", "TimephasedData",
]
DEPOIS_DE_STATUS = [
    "CurrentDate", "MicrosoftProjectServerURL", "Autolink", "NewTaskStartDate", "NewTasksAreManual",
    "DefaultTaskEVMethod", "ProjectExternallyEdited", "ExtendedCreationDate", "ActualsInSync",
    "RemoveFileProperties", "AdminProject", "UpdateManuallyScheduledTasksWhenEditingLinks",
    "KeepTaskOnNearestWorkingTimeWhenMadeAutoScheduled", "OutlineCodes", "WBSMasks", "ExtendedAttributes",
    "Calendars", "Tasks",
]


def _definir(el: ET.Element, nome: str, valor: str) -> None:
    """Grava um campo na tarefa, criando-o na posição correta se não existir."""
    k = _filho(el, nome)
    if k is not None:
        k.text = valor
        return
    novo = ET.Element(Q + nome)
    novo.text = valor
    idx = ORDEM_TAREFA.index(nome)
    for i, c in enumerate(list(el)):
        nome_c = c.tag.replace(Q, "")
        if nome_c in ORDEM_TAREFA and ORDEM_TAREFA.index(nome_c) > idx:
            el.insert(i, novo)
            return
    el.append(novo)


def _remover(el: ET.Element, nome: str) -> None:
    k = _filho(el, nome)
    if k is not None:
        el.remove(k)


# ----------------------------------------------------------------------------
# Modelo
# ----------------------------------------------------------------------------
@dataclass
class Tarefa:
    el: ET.Element
    uid: str
    id: str
    nome: str
    eap: str
    nivel: int
    marco: bool
    inicio: dt.datetime | None
    termino: dt.datetime | None
    dur_h: float
    pct: float
    fisico: float
    lb_inicio: dt.datetime | None
    lb_termino: dt.datetime | None
    campos: dict[str, float] = field(default_factory=dict)
    pai: "Tarefa | None" = None
    filhos: list["Tarefa"] = field(default_factory=list)

    @property
    def resumo(self) -> bool:
        return bool(self.filhos)


class Projeto:
    def __init__(self, xml: str, nome_arquivo: str = "cronograma.xml"):
        try:
            self.raiz = ET.fromstring(xml.encode("utf-8") if isinstance(xml, str) else xml)
        except ET.ParseError as e:
            raise ErroCronograma("O arquivo não é um XML válido.") from e
        if self.raiz.tag != Q + "Project" or _filho(self.raiz, "Tasks") is None:
            raise ErroCronograma("Este XML não parece ter sido salvo pelo MS Project (faltam as tarefas).")
        self.arquivo = nome_arquivo
        self.titulo = _txt(self.raiz, "Title") or _txt(self.raiz, "Name") or os.path.splitext(nome_arquivo)[0]

        nomes_campos: dict[str, str] = {}
        ea = _filho(self.raiz, "ExtendedAttributes")
        if ea is not None:
            for d in ea.findall(Q + "ExtendedAttribute"):
                nomes_campos[_txt(d, "FieldID")] = _txt(d, "Alias") or _txt(d, "FieldName")

        self.tarefas: list[Tarefa] = []
        self.por_uid: dict[str, Tarefa] = {}
        usados: set[str] = set()
        pilha: list[Tarefa] = []
        for el in _filho(self.raiz, "Tasks").findall(Q + "Task"):
            if _txt(el, "IsNull") == "1":
                continue
            lb_i = lb_t = None
            for b in el.findall(Q + "Baseline"):
                if _txt(b, "Number") in ("", "0"):
                    lb_i, lb_t = data(_txt(b, "Start")), data(_txt(b, "Finish"))
            campos = {}
            for x in el.findall(Q + "ExtendedAttribute"):
                v = num(_txt(x, "Value"))
                if v is not None:
                    campos[_txt(x, "FieldID")] = v
                    usados.add(_txt(x, "FieldID"))
            t = Tarefa(
                el=el, uid=_txt(el, "UID"), id=_txt(el, "ID"), nome=_txt(el, "Name") or "(sem nome)",
                eap=_txt(el, "WBS") or _txt(el, "OutlineNumber"), nivel=int(_txt(el, "OutlineLevel") or 0),
                marco=_txt(el, "Milestone") == "1", inicio=data(_txt(el, "Start")), termino=data(_txt(el, "Finish")),
                dur_h=horas(_txt(el, "Duration")), pct=float(_txt(el, "PercentComplete") or 0),
                fisico=float(_txt(el, "PhysicalPercentComplete") or 0), lb_inicio=lb_i, lb_termino=lb_t, campos=campos,
            )
            while pilha and pilha[-1].nivel >= t.nivel:
                pilha.pop()
            t.pai = pilha[-1] if pilha else None
            if t.pai:
                t.pai.filhos.append(t)
            pilha.append(t)
            self.tarefas.append(t)
            self.por_uid[t.uid] = t
        if not self.tarefas:
            raise ErroCronograma("Nenhuma tarefa encontrada no arquivo.")

        self.campos_peso = [{"id": f, "nome": nomes_campos.get(f, f"Campo {f}")} for f in sorted(usados)]
        auto = next((c for c in self.campos_peso if re.search(r"peso|weight", c["nome"], re.I)), None)
        self.peso = auto["id"] if auto else "dur"
        usa_fisico = any(t.fisico > 0 for t in self.tarefas)
        self.medicao_arquivo = {t.uid: (t.fisico if usa_fisico else t.pct) for t in self.tarefas}
        self.data_status = data(_txt(self.raiz, "StatusDate"))

    # ------------------------------------------------------------------ pesos
    def peso_de(self, t: Tarefa, criterio: str | None = None) -> float:
        criterio = criterio or self.peso
        if criterio == "dur":
            return 0.0 if t.marco else max(t.dur_h, 0.0)
        return t.campos.get(criterio, 0.0) or 0.0

    @staticmethod
    def previsto(t: Tarefa, em: dt.datetime) -> float:
        i = t.lb_inicio or t.inicio
        f = t.lb_termino or t.termino
        if not i or not f:
            return 0.0
        if em <= i:
            return 0.0
        if em >= f:
            return 100.0
        return (em - i) / (f - i) * 100

    def agregar(self, atual: dict, anterior: dict, data_status: dt.datetime, criterio: str | None = None) -> dict:
        """Soma ponderada: devolve {uid: {w, real, ant, prev}} e 'total'."""
        A: dict = {}

        def ir(t: Tarefa) -> dict:
            if not t.filhos:
                w = self.peso_de(t, criterio)
                A[t.uid] = {"w": w, "r": w * float(atual.get(t.uid, 0)), "b": w * float(anterior.get(t.uid, 0)),
                            "p": w * self.previsto(t, data_status)}
                return A[t.uid]
            s = {"w": 0.0, "r": 0.0, "b": 0.0, "p": 0.0}
            for c in t.filhos:
                x = ir(c)
                for k in s:
                    s[k] += x[k]
            A[t.uid] = s
            return s

        tot = {"w": 0.0, "r": 0.0, "b": 0.0, "p": 0.0}
        for t in self.tarefas:
            if t.pai is None:
                x = ir(t)
                for k in tot:
                    tot[k] += x[k]
        A["total"] = tot
        return A

    @staticmethod
    def pct(a: dict, chave: str) -> float:
        return a[chave] / a["w"] if a and a["w"] > 0 else 0.0

    # --------------------------------------------------------------- exportar
    def exportar_xml(self, atual: dict, data_status: dt.datetime) -> str:
        """XML para abrir no MS Project com a medição aplicada."""
        for t in self.tarefas:
            if t.filhos:
                continue
            p = max(0, min(100, round(float(atual.get(t.uid, 0)))))
            el = t.el
            _definir(el, "PercentComplete", str(p))
            _definir(el, "PhysicalPercentComplete", str(p))
            if not t.marco:
                _definir(el, "ActualDuration", pt_dur(t.dur_h * p / 100))
                _definir(el, "RemainingDuration", pt_dur(t.dur_h * (100 - p) / 100))
            if p > 0:
                if not _txt(el, "ActualStart"):
                    _definir(el, "ActualStart", _txt(el, "Start"))
            else:
                _remover(el, "ActualStart")
            if p >= 100:
                _definir(el, "ActualFinish", _txt(el, "Finish"))
            else:
                _remover(el, "ActualFinish")

        valor = data_status.strftime("%Y-%m-%dT17:00:00")
        sd = _filho(self.raiz, "StatusDate")
        if sd is not None:
            sd.text = valor
        else:
            sd = ET.Element(Q + "StatusDate")
            sd.text = valor
            pos = len(self.raiz)
            for nome in DEPOIS_DE_STATUS:
                k = _filho(self.raiz, nome)
                if k is not None:
                    pos = list(self.raiz).index(k)
                    break
            self.raiz.insert(pos, sd)
        corpo = ET.tostring(self.raiz, encoding="unicode")
        return '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n' + corpo

    def _linhas_boletim(self, atual, anterior, data_status, criterio=None):
        A = self.agregar(atual, anterior, data_status, criterio)
        for t in self.tarefas:
            a = A[t.uid]
            if t.filhos:
                pa, pr, pp = self.pct(a, "b"), self.pct(a, "r"), self.pct(a, "p")
            else:
                pa, pr, pp = float(anterior.get(t.uid, 0)), float(atual.get(t.uid, 0)), self.previsto(t, data_status)
            yield t, a["w"], pa, pr, pp
        yield None, A["total"], None, None, None

    def nome_criterio(self, criterio=None) -> str:
        criterio = criterio or self.peso
        if criterio == "dur":
            return "Duração"
        return next((c["nome"] for c in self.campos_peso if c["id"] == criterio), criterio)

    def exportar_csv(self, atual, anterior, data_status, criterio=None) -> str:
        """Boletim de medição em CSV (; e vírgula decimal) — formato lido pela macro."""
        linhas = list(self._linhas_boletim(atual, anterior, data_status, criterio))
        tot = linhas.pop()[1]
        out = io.StringIO()
        w = csv.writer(out, delimiter=";", lineterminator="\r\n")
        w.writerow(["Boletim de medição", self.titulo])
        w.writerow(["Data de status", br_data(data_status)])
        w.writerow(["Critério de peso", self.nome_criterio(criterio)])
        w.writerow(["Avanço físico anterior", br(self.pct(tot, "b")) + "%"])
        w.writerow(["Avanço físico atual", br(self.pct(tot, "r")) + "%"])
        w.writerow(["Previsto na data de status", br(self.pct(tot, "p")) + "%"])
        w.writerow([])
        w.writerow(["UID", "Id", "Resumo", "% atual", "% anterior", "Avanço no período (p.p.)", "% previsto", "Peso",
                    "EAP", "Tarefa", "Nível", "Início", "Término"])
        for t, peso, pa, pr, pp in linhas:
            w.writerow([t.uid, t.id, "Sim" if t.filhos else "Não", br(pr), br(pa), br(pr - pa), br(pp), br(peso),
                        t.eap, t.nome, t.nivel, br_data(t.inicio), br_data(t.termino)])
        return "﻿" + out.getvalue()

    def exportar_xlsx(self, atual, anterior, data_status, criterio=None) -> bytes:
        """Boletim de medição em Excel, com a EAP agrupada por nível."""
        from openpyxl import Workbook
        from openpyxl.styles import Alignment, Border, Font, PatternFill, Side

        linhas = list(self._linhas_boletim(atual, anterior, data_status, criterio))
        tot = linhas.pop()[1]
        wb = Workbook()
        ws = wb.active
        ws.title = "Boletim de medição"
        tinta = "101922"
        ws["A1"] = "Boletim de medição"
        ws["A1"].font = Font(size=16, bold=True, color=tinta)
        ws["A2"] = self.titulo
        ws["A2"].font = Font(size=12, color="515C66")
        resumo = [
            ("Data de status", data_status.date(), "dd/mm/yyyy"),
            ("Critério de peso", self.nome_criterio(criterio), None),
            ("Avanço físico anterior", self.pct(tot, "b") / 100, "0.0%"),
            ("Avanço físico atual", self.pct(tot, "r") / 100, "0.0%"),
            ("Previsto na data de status", self.pct(tot, "p") / 100, "0.0%"),
            ("Desvio (real − previsto)", (self.pct(tot, "r") - self.pct(tot, "p")) / 100, "+0.0%;-0.0%"),
        ]
        for i, (rot, val, fmtn) in enumerate(resumo, start=4):
            ws.cell(i, 1, rot).font = Font(color="515C66")
            c = ws.cell(i, 2, val)
            c.font = Font(bold=True)
            if fmtn:
                c.number_format = fmtn

        cab = ["UID", "Id", "EAP", "Tarefa", "Início", "Término", "Peso", "% anterior", "% atual",
               "Avanço no período", "% previsto"]
        lin0 = 11
        linha_fina = Side(style="thin", color="D3D8D1")
        for j, h in enumerate(cab, start=1):
            c = ws.cell(lin0, j, h)
            c.font = Font(bold=True, color="FFFFFF")
            c.fill = PatternFill("solid", fgColor=tinta)
            c.alignment = Alignment(vertical="center")
        ws.row_dimensions[lin0].height = 22
        nivel_min = min(t.nivel for t in self.tarefas)
        for i, (t, peso, pa, pr, pp) in enumerate(linhas, start=lin0 + 1):
            vals = [int(t.uid) if t.uid.isdigit() else t.uid, int(t.id) if t.id.isdigit() else t.id, t.eap,
                    ("   " * (t.nivel - nivel_min)) + t.nome,
                    t.inicio.date() if t.inicio else None, t.termino.date() if t.termino else None,
                    round(peso / 8, 2) if (criterio or self.peso) == "dur" else round(peso, 4),
                    pa / 100, pr / 100, (pr - pa) / 100, pp / 100]
            for j, v in enumerate(vals, start=1):
                c = ws.cell(i, j, v)
                c.border = Border(bottom=linha_fina)
                if j in (5, 6):
                    c.number_format = "dd/mm/yyyy"
                if j >= 8:
                    c.number_format = "0.0%"
                if t.filhos:
                    c.font = Font(bold=True)
            if not t.filhos and round(pr) != round(pa):
                ws.cell(i, 9).fill = PatternFill("solid", fgColor="FCEFC7")
            nivel = max(0, t.nivel - nivel_min)
            if nivel:
                ws.row_dimensions[i].outline_level = min(nivel, 7)
        larguras = [8, 7, 12, 60, 12, 12, 9, 12, 11, 17, 12]
        for j, w_ in enumerate(larguras, start=1):
            ws.column_dimensions[ws.cell(lin0, j).column_letter].width = w_
        ws.freeze_panes = ws.cell(lin0 + 1, 5)
        ws.auto_filter.ref = f"A{lin0}:K{lin0 + len(linhas)}"
        ws.sheet_properties.outlinePr.summaryBelow = False
        buf = io.BytesIO()
        wb.save(buf)
        return buf.getvalue()


def nome_base(arquivo: str) -> str:
    base = os.path.splitext(os.path.basename(arquivo or "cronograma"))[0]
    return re.sub(r"_+", "_", re.sub(r"[^\w\-]+", "_", base)).strip("_") or "cronograma"
