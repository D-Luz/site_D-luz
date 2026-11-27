from openpyxl import Workbook
from openpyxl.styles import Font, Alignment, PatternFill, Border, Side

def criar_orcamento_inteligente():
    # Cria o ficheiro de Excel (Workbook)
    wb = Workbook()
    
    # =========================================================
    # 1. SEPARADOR DE LANÇAMENTO (O "BACK-END" PARA O VENDEDOR)
    # =========================================================
    ws_lancamento = wb.active
    ws_lancamento.title = "Lançamento"
    
    # Cabeçalhos simplificados para o vendedor digitar
    headers_lancamento = ['Ambiente', 'Produto', 'Qtd', 'V. Unitário', 'V. Total']
    ws_lancamento.append(headers_lancamento)
    
    # Formatação básica do cabeçalho
    for col in range(1, 6):
        cell = ws_lancamento.cell(row=1, column=col)
        cell.font = Font(bold=True)
        cell.fill = PatternFill(start_color="D9D9D9", end_color="D9D9D9", fill_type="solid")
    
    # Inserir alguns dados de exemplo (baseados no seu ficheiro MURILO E GABI)
    dados_exemplo = [
        ['GARAGEM', 'PERFIL NO FRAME LARGO 2 MTS', 4, 398.00, "=C2*D2"],
        ['GARAGEM', 'PERFIL NO FRAME LARGO 3 MTS', 2, 599.00, "=C3*D3"],
        ['VARANDA', 'PERFIL SOBREPOR 2 MTS PT', 2, 272.25, "=C4*D4"]
    ]
    for row in dados_exemplo:
        ws_lancamento.append(row)
        
    # Ajustar a largura das colunas para facilitar a digitação
    ws_lancamento.column_dimensions['A'].width = 20
    ws_lancamento.column_dimensions['B'].width = 45
    ws_lancamento.column_dimensions['C'].width = 10
    ws_lancamento.column_dimensions['D'].width = 15
    ws_lancamento.column_dimensions['E'].width = 15

    # =========================================================
    # 2. SEPARADOR DE PROPOSTA (O "FRONT-END" PARA O CLIENTE/PDF)
    # =========================================================
    ws_proposta = wb.create_sheet(title="Proposta")
    ws_proposta.sheet_view.showGridLines = False  # Remove as linhas de grelha
    
    # Cores da sua marca (pode alterar os códigos HEX depois)
    BRAND_COLOR = "2C3E50" # Azul escuro
    LIGHT_GRAY = "F8F9FA"  # Cinza claro para o efeito zebrado
    
    # Cabeçalho do Documento
    ws_proposta.merge_cells('A1:C2')
    ws_proposta['A1'] = "PROPOSTA COMERCIAL"
    ws_proposta['A1'].font = Font(name='Arial', size=20, bold=True, color=BRAND_COLOR)
    ws_proposta['A1'].alignment = Alignment(vertical='center')

    ws_proposta.merge_cells('D1:E2')
    ws_proposta['D1'] = "[ LOGOTIPO / NOME DA EMPRESA ]"
    ws_proposta['D1'].font = Font(name='Arial', size=11, bold=True, color="7F8C8D")
    ws_proposta['D1'].alignment = Alignment(horizontal='right', vertical='center')
    
    # Dados do Cliente (Estes também poderiam vir do separador Lançamento)
    ws_proposta['A4'] = "Cliente:"
    ws_proposta['A4'].font = Font(bold=True, color=BRAND_COLOR)
    ws_proposta['B4'] = "Murilo e Gabi" 
    
    ws_proposta['A5'] = "Data:"
    ws_proposta['A5'].font = Font(bold=True, color=BRAND_COLOR)
    ws_proposta['B5'] = "08/06/2026"

    # Cabeçalhos da Tabela Visível
    for col_num, header in enumerate(headers_lancamento, 1):
        cell = ws_proposta.cell(row=7, column=col_num)
        cell.value = header
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill(start_color=BRAND_COLOR, end_color=BRAND_COLOR, fill_type="solid")
        cell.alignment = Alignment(horizontal='center', vertical='center')
    ws_proposta.row_dimensions[7].height = 25

    # FÓRMULAS: O segredo para puxar os dados do Back-end sem intervenção humana
    thin_border = Border(bottom=Side(style='thin', color='DDDDDD'))
    
    # Vamos pré-configurar 50 linhas com fórmulas e formatação
    for i in range(50):
        row_num = i + 8          # Linha atual no separador Proposta
        row_lancamento = i + 2   # Linha correspondente no separador Lançamento
        
        # Efeito zebrado subtil
        fill_color = "FFFFFF" if i % 2 == 0 else LIGHT_GRAY
        row_fill = PatternFill(start_color=fill_color, end_color=fill_color, fill_type="solid")
        
        for col_num, letter in enumerate(['A', 'B', 'C', 'D', 'E'], 1):
            cell = ws_proposta.cell(row=row_num, column=col_num)
            
            # A fórmula: Se a célula no Lançamento for vazia, fica vazia. Senão, puxa o valor.
            formula = f'=SE(Lançamento!{letter}{row_lancamento}="","",Lançamento!{letter}{row_lancamento})'
            cell.value = formula
            
            # Aplica o design
            cell.border = thin_border
            cell.fill = row_fill
            cell.alignment = Alignment(vertical='center')
            
            # Formatação monetária e alinhamento
            if letter in ['D', 'E']:
                cell.number_format = 'R$ #,##0.00'
            if letter in ['C']:
                cell.alignment = Alignment(horizontal='center', vertical='center')

    # Ajustar as larguras no Front-end (Proposta)
    ws_proposta.column_dimensions['A'].width = 18
    ws_proposta.column_dimensions['B'].width = 45
    ws_proposta.column_dimensions['C'].width = 10
    ws_proposta.column_dimensions['D'].width = 18
    ws_proposta.column_dimensions['E'].width = 18

    # Configuração de Impressão (PDF)
    ws_proposta.page_setup.paperSize = ws_proposta.PAPERSIZE_A4
    ws_proposta.page_setup.fitToPage = True
    ws_proposta.page_setup.fitToWidth = 1
    ws_proposta.page_setup.fitToHeight = 0
    ws_proposta.page_margins.left = 0.5
    ws_proposta.page_margins.right = 0.5

    # Guardar o ficheiro
    nome_ficheiro = "Orcamento_Master_Automatizado.xlsx"
    wb.save(nome_ficheiro)
    print(f"✅ Sucesso! O ficheiro '{nome_ficheiro}' foi gerado.")

if __name__ == "__main__":
    criar_orcamento_inteligente()