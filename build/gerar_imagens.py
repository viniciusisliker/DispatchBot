"""Gera a identidade visual do DispatchBot em assets/:
- icone.ico                       ícone dos executáveis e do instalador
- instalador/pequena-*.png        marca no canto das páginas do instalador (transparente: serve no claro e no escuro)
- instalador/grande-*.png         painel lateral das páginas de boas-vindas e conclusão
Tudo é desenhado em alta resolução e reduzido (antisserrilhado).
"""
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

RAIZ = Path(__file__).resolve().parent.parent
ASSETS = RAIZ / "assets"
TEAL = (14, 107, 102)
TEAL_ESCURO = (8, 62, 60)
TEAL_CLARO = (143, 211, 201)
BRANCO = (255, 255, 255)
FONTES = Path(r"C:\Windows\Fonts")


def fonte(nome: str, tamanho: int):
    try:
        return ImageFont.truetype(str(FONTES / nome), tamanho)
    except OSError:
        return ImageFont.load_default()


def marca(lado: int, fundo=TEAL) -> Image.Image:
    """Quadrado arredondado com o rosto do robô e uma seta de despacho no lugar da boca."""
    s = lado * 4  # superamostragem
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    u = s / 64
    d.rounded_rectangle((2 * u, 2 * u, 62 * u, 62 * u), radius=15 * u, fill=fundo)
    # antena
    d.line((32 * u, 9 * u, 32 * u, 15 * u), fill=BRANCO, width=int(3 * u))
    d.ellipse((29 * u, 6 * u, 35 * u, 12 * u), fill=BRANCO)
    # cabeça
    d.rounded_rectangle((14 * u, 16 * u, 50 * u, 48 * u), radius=8 * u, outline=BRANCO, width=int(4.5 * u))
    # olhos
    d.ellipse((22 * u, 25 * u, 28.5 * u, 31.5 * u), fill=BRANCO)
    d.ellipse((35.5 * u, 25 * u, 42 * u, 31.5 * u), fill=BRANCO)
    # seta de despacho
    d.line((23 * u, 39 * u, 38 * u, 39 * u), fill=BRANCO, width=int(3.5 * u))
    d.polygon([(37 * u, 34.5 * u), (43 * u, 39 * u), (37 * u, 43.5 * u)], fill=BRANCO)
    return img.resize((lado, lado), Image.LANCZOS)


def marca_avatar(lado: int) -> Image.Image:
    """Avatar do robô (assets/avatar.png) inteiro, quadrado (o mesmo da bandeja,
    sem a bolinha de status). Sem o arquivo, usa a marca desenhada."""
    try:
        rosto = Image.open(ASSETS / "avatar.png").convert("RGBA")
    except OSError:
        return marca(lado)
    s = lado * 4
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    img.paste(rosto.resize((s, s), Image.LANCZOS), (0, 0))
    return img.resize((lado, lado), Image.LANCZOS)


CINZA = (52, 56, 58)          # topo da coluna lateral do instalador
CINZA_ESCURO = (28, 30, 32)   # base
CINZA_PONTO = (70, 75, 78)
CINZA_TEXTO = (196, 202, 204)
FRASE = "Abra seu chamado de forma automática"


def quebrar(d, texto: str, f, largura_max: float) -> list:
    """Quebra o texto em linhas que cabem na largura (palavra por palavra)."""
    linhas, atual = [], ""
    for palavra in texto.split():
        teste = f"{atual} {palavra}".strip()
        if atual and d.textlength(teste, font=f) > largura_max:
            linhas.append(atual)
            atual = palavra
        else:
            atual = teste
    return linhas + ([atual] if atual else [])


def painel(largura: int, altura: int) -> Image.Image:
    """Coluna lateral do instalador: degradê cinza escuro, avatar, nome e a frase do robô."""
    s = 3
    L, A = largura * s, altura * s
    img = Image.new("RGB", (L, A))
    d = ImageDraw.Draw(img)
    for y in range(A):
        t = y / A
        cor = tuple(round(CINZA[i] + (CINZA_ESCURO[i] - CINZA[i]) * t) for i in range(3))
        d.line((0, y, L, y), fill=cor)
    # malha discreta de pontos (fila de chamados)
    passo = L // 9
    for yy in range(int(A * 0.74), A, passo):
        for xx in range(passo // 2, L, passo):
            r = L * 0.006
            d.ellipse((xx - r, yy - r, xx + r, yy + r), fill=CINZA_PONTO)

    lado = int(L * 0.42)
    m = marca_avatar(lado)  # avatar sobre o teal
    img.paste(m, ((L - lado) // 2, int(A * 0.2)), m)

    nome = fonte("segoeuib.ttf", int(L * 0.13))
    sub = fonte("segoeui.ttf", int(L * 0.068))
    y = int(A * 0.2) + lado + int(A * 0.04)
    linhas = [("DispatchBot", nome, BRANCO, 0.03)]
    linhas += [(t, sub, CINZA_TEXTO, 0) for t in quebrar(d, FRASE, sub, L * 0.84)]
    for texto, f, cor, espaco in linhas:
        w = d.textlength(texto, font=f)
        d.text(((L - w) / 2, y), texto, font=f, fill=cor)
        y += int(f.size * 1.25 + A * espaco)
    return img.resize((largura, altura), Image.LANCZOS)


def main():
    (ASSETS / "instalador").mkdir(parents=True, exist_ok=True)
    marca_avatar(256).save(ASSETS / "icone.ico", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    for lado in (58, 97, 159):
        marca_avatar(lado).save(ASSETS / "instalador" / f"pequena-{lado}.png")
    for largura, altura in ((202, 386), (336, 643), (534, 1022)):
        painel(largura, altura).save(ASSETS / "instalador" / f"grande-{largura}.png")
    print("assets gerados")


if __name__ == "__main__":
    main()
