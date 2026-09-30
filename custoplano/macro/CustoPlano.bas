Attribute VB_Name = "CustoPlano"
Option Explicit

' =====================================================================
'  Custo Plano - integração do MS Project com o sistema de planejamento
'
'  EnviarParaCustoPlano
'     Salva o .mpp aberto e gera, na mesma pasta, uma cópia
'     "<nome>_CustoPlano.xml" para importar na tela Project do sistema.
'     O seu arquivo continua sendo o .mpp.
'
'  ReceberMedicaoCustoPlano
'     Lê o boletim de medição (.csv) baixado do sistema e grava no .mpp
'     aberto o % concluído e o % físico de cada tarefa (pelo UID) e a
'     data de status. Depois é só salvar o .mpp.
' =====================================================================

Public Sub EnviarParaCustoPlano()
    Dim mpp As String, xmlPath As String, nome As String
    If Projects.Count = 0 Then Exit Sub
    If ActiveProject.Path = "" Then
        MsgBox "Salve o cronograma como .mpp antes de enviar.", vbExclamation, "Custo Plano"
        Exit Sub
    End If
    mpp = ActiveProject.FullName
    nome = ActiveProject.Name
    If InStrRev(nome, ".") > 0 Then nome = Left(nome, InStrRev(nome, ".") - 1)
    xmlPath = ActiveProject.Path & Application.PathSeparator & nome & "_CustoPlano.xml"

    FileSave
    Application.Alerts False
    FileSaveAs Name:=xmlPath, FormatID:="MSProject.XML"
    FileCloseEx pjDoNotSave
    FileOpenEx Name:=mpp
    Application.Alerts True

    MsgBox "Arquivo gerado:" & vbCrLf & xmlPath & vbCrLf & vbCrLf & _
           "Importe este arquivo na tela Project do sistema Custo Plano." & vbCrLf & _
           "O seu cronograma .mpp continua aberto e salvo.", vbInformation, "Custo Plano"
End Sub

Public Sub ReceberMedicaoCustoPlano()
    Dim arq As String, f As Integer, linha As String, c() As String
    Dim dados As Boolean, gravadas As Long, naoAchadas As Long, ignoradas As Long
    Dim t As Task, pct As Double, uid As Long, sd As Variant

    If Projects.Count = 0 Then
        MsgBox "Abra o cronograma (.mpp) que vai receber a medição.", vbExclamation, "Custo Plano"
        Exit Sub
    End If
    arq = EscolherArquivo()
    If arq = "" Then Exit Sub

    f = FreeFile
    Open arq For Input As #f
    Do While Not EOF(f)
        Line Input #f, linha
        linha = Replace(linha, Chr(239) & Chr(187) & Chr(191), "")
        c = Split(linha, ";")
        If Not dados Then
            If UBound(c) >= 1 Then
                If Trim(c(0)) = "Data de status" Then sd = LerData(c(1))
                If Trim(c(0)) = "UID" Then dados = True
            End If
        ElseIf UBound(c) >= 3 Then
            If IsNumeric(c(0)) Then
                If UCase(Left(Trim(c(2)), 1)) = "S" Then
                    ignoradas = ignoradas + 1
                Else
                    uid = CLng(c(0))
                    pct = Val(Replace(c(3), ",", "."))
                    If pct < 0 Then pct = 0
                    If pct > 100 Then pct = 100
                    Set t = Nothing
                    On Error Resume Next
                    Set t = ActiveProject.Tasks.UniqueID(uid)
                    On Error GoTo 0
                    If t Is Nothing Then
                        naoAchadas = naoAchadas + 1
                    ElseIf t.Summary Then
                        ignoradas = ignoradas + 1
                    Else
                        t.PercentComplete = Round(pct, 0)
                        t.PhysicalPercentComplete = Round(pct, 0)
                        gravadas = gravadas + 1
                    End If
                End If
            End If
        End If
    Loop
    Close #f

    If Not dados Then
        MsgBox "Este arquivo não é um boletim de medição do Custo Plano.", vbExclamation, "Custo Plano"
        Exit Sub
    End If
    If Not IsEmpty(sd) Then ActiveProject.StatusDate = sd

    MsgBox "Medição gravada em " & gravadas & " tarefas." & vbCrLf & _
           IIf(IsEmpty(sd), "", "Data de status: " & Format(sd, "dd/mm/yyyy") & vbCrLf) & _
           IIf(naoAchadas > 0, naoAchadas & " tarefas do boletim não existem neste cronograma (UID diferente)." & vbCrLf, "") & _
           vbCrLf & "Confira e salve o .mpp (Ctrl+B).", vbInformation, "Custo Plano"
End Sub

Private Function LerData(ByVal s As String) As Variant
    Dim p() As String, a As Integer
    p = Split(Trim(s), "/")
    If UBound(p) <> 2 Then Exit Function
    a = CInt(p(2))
    If a < 100 Then a = a + 2000
    LerData = DateSerial(a, CInt(p(1)), CInt(p(0))) + TimeSerial(17, 0, 0)
End Function

Private Function EscolherArquivo() As String
    Dim xl As Object, fd As Object
    On Error GoTo semExcel
    Set xl = CreateObject("Excel.Application")
    Set fd = xl.FileDialog(3)
    fd.Title = "Escolha o boletim de medição (.csv) do Custo Plano"
    fd.Filters.Clear
    fd.Filters.Add "Boletim de medição", "*.csv"
    fd.AllowMultiSelect = False
    If fd.Show = -1 Then EscolherArquivo = fd.SelectedItems(1)
    xl.Quit
    Set xl = Nothing
    Exit Function
semExcel:
    On Error Resume Next
    If Not xl Is Nothing Then xl.Quit
    EscolherArquivo = InputBox("Cole o caminho completo do boletim de medição (.csv):", "Custo Plano")
End Function
