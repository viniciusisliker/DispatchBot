"""Desenha a coruja do DispatchBot (assets/coruja.png, 1024x1024), no mesmo estilo
chapado do avatar: fundo escuro, corpo marrom, olhos grandes (legíveis em 16 px)."""
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

ASSETS = Path(__file__).resolve().parent.parent / "assets"

LADO = 1024
S = LADO * 2  # desenha em dobro e reduz (borda lisa)


def coruja() -> Image.Image:
    u = S / 100  # 1 unidade = 1% do lado
    img = Image.new("RGBA", (S, S))
    # fundo: degradê radial cinza escuro (como o do avatar)
    fundo = Image.radial_gradient("L").resize((S, S))
    escuro = Image.new("RGBA", (S, S), (34, 35, 38, 255))
    claro = Image.new("RGBA", (S, S), (58, 59, 64, 255))
    img = Image.composite(escuro, claro, fundo)
    d = ImageDraw.Draw(img)

    marrom = (124, 88, 62)
    marrom_escuro = (88, 60, 42)
    peito = (232, 212, 186)
    disco = (246, 232, 212)
    olho = (255, 184, 28)
    preto = (28, 28, 30)
    bico = (240, 140, 40)

    # orelhas (tufos)
    d.polygon([(22 * u, 34 * u), (20 * u, 8 * u), (42 * u, 24 * u)], fill=marrom_escuro)
    d.polygon([(78 * u, 34 * u), (80 * u, 8 * u), (58 * u, 24 * u)], fill=marrom_escuro)
    # corpo
    d.ellipse((14 * u, 18 * u, 86 * u, 112 * u), fill=marrom)
    # asas
    d.ellipse((8 * u, 52 * u, 30 * u, 104 * u), fill=marrom_escuro)
    d.ellipse((70 * u, 52 * u, 92 * u, 104 * u), fill=marrom_escuro)
    # peito com penas em "V"
    d.ellipse((30 * u, 62 * u, 70 * u, 110 * u), fill=peito)
    for linha, y in enumerate((74, 82, 90)):
        for i in range(3 + (linha % 2)):
            x = 40 + i * 7 - (linha % 2) * 3.5
            d.arc((x * u, y * u, (x + 6) * u, (y + 5) * u), 20, 160, fill=marrom, width=round(1.2 * u))
    # disco facial (dois círculos claros)
    d.ellipse((18 * u, 26 * u, 52 * u, 60 * u), fill=disco)
    d.ellipse((48 * u, 26 * u, 82 * u, 60 * u), fill=disco)
    # olhos: íris amarela, pupila, brilho
    for cx in (35, 65):
        d.ellipse(((cx - 12) * u, 31 * u, (cx + 12) * u, 55 * u), fill=olho)
        d.ellipse(((cx - 7) * u, 36 * u, (cx + 7) * u, 50 * u), fill=preto)
        d.ellipse(((cx - 4) * u, 38 * u, (cx - 0.5) * u, 41.5 * u), fill="white")
    # óculos (como o avatar)
    larg = round(2.2 * u)
    d.rounded_rectangle((20 * u, 28 * u, 49 * u, 57 * u), radius=9 * u, outline=preto, width=larg)
    d.rounded_rectangle((51 * u, 28 * u, 80 * u, 57 * u), radius=9 * u, outline=preto, width=larg)
    d.line((48 * u, 37 * u, 52 * u, 37 * u), fill=preto, width=larg)
    # sobrancelhas (penas acima dos olhos)
    d.polygon([(22 * u, 27 * u), (48 * u, 31 * u), (47 * u, 25 * u)], fill=marrom_escuro)
    d.polygon([(78 * u, 27 * u), (52 * u, 31 * u), (53 * u, 25 * u)], fill=marrom_escuro)
    # bico
    d.polygon([(45 * u, 56 * u), (55 * u, 56 * u), (50 * u, 66 * u)], fill=bico)

    return img.resize((LADO, LADO), Image.LANCZOS)


if __name__ == "__main__":
    coruja().save(ASSETS / "coruja.png")
    print("ok:", ASSETS / "coruja.png")
