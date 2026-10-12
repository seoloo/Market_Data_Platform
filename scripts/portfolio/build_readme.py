"""포트폴리오 보고서 → README.md (GitHub Markdown).

PDF 와 같은 원본(build_report.build)을 쓴다. 그림 SVG · 화면 JPEG 은 docs/assets/portfolio/ 에 파일로 둔다.
python -m scripts.portfolio.build_readme
"""

from __future__ import annotations

import base64
import re
import sys
from html.parser import HTMLParser

from scripts.portfolio import build_report as br
from scripts.portfolio.cards_base import VERDICTS

ROOT = br.ROOT
ASSET_DIR = ROOT / "docs" / "assets" / "portfolio"
ASSET_REL = "docs/assets/portfolio"
EMOJI = {
    "통과": "🟢",
    "역사적 지지": "🟩",
    "설계 결정": "🟣",
    "판단 불가": "🟠",
    "자료 부족": "🔵",
    "기각": "⚪",
}
VOID = {"img", "br", "meta", "link", "hr"}


class Node:
    def __init__(self, tag: str, attrs: dict[str, str], parent: Node | None) -> None:
        self.tag, self.attrs, self.parent = tag, attrs, parent
        self.kids: list[Node | str] = []

    @property
    def cls(self) -> set[str]:
        return set(self.attrs.get("class", "").split())

    def find(self, tag: str | None = None, cls: str | None = None) -> list[Node]:
        out: list[Node] = []
        for k in self.kids:
            if isinstance(k, Node):
                if (tag is None or k.tag == tag) and (cls is None or cls in k.cls):
                    out.append(k)
                out += k.find(tag, cls)
        return out


class Tree(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = Node("root", {}, None)
        self.cur = self.root

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        n = Node(tag, {k: v or "" for k, v in attrs}, self.cur)
        self.cur.kids.append(n)
        if tag not in VOID:
            self.cur = n

    def handle_endtag(self, tag: str) -> None:
        n: Node | None = self.cur
        while n is not None and n.tag != tag:
            n = n.parent
        if n is not None and n.parent is not None:
            self.cur = n.parent

    def handle_data(self, data: str) -> None:
        self.cur.kids.append(data)


# ------------------------------------------------------------------ 자산 빼내기
def extract_assets(html: str) -> str:
    ASSET_DIR.mkdir(parents=True, exist_ok=True)
    for old in ASSET_DIR.glob("*"):
        old.unlink()
    n = 0

    def svg(m: re.Match[str]) -> str:
        nonlocal n
        n += 1
        s = m.group(0)
        vb = re.search(r'viewBox="0 0 (\d+) (\d+)"', s)
        w, h = (vb.group(1), vb.group(2)) if vb else ("760", "300")
        s = s.replace('width="100%"', f'width="{w}" height="{h}"', 1)
        s = s.replace("<svg ", '<svg style="background:#ffffff" ', 1)
        name = f"fig{n:02d}.svg"
        (ASSET_DIR / name).write_text(s, encoding="utf-8")
        return f'<img src="{ASSET_REL}/{name}">'

    def jpg(m: re.Match[str]) -> str:
        nonlocal n
        n += 1
        name = f"shot{n:02d}.jpg"
        (ASSET_DIR / name).write_bytes(base64.b64decode(m.group(1)))
        return f'src="{ASSET_REL}/{name}"'

    html = re.sub(r"<svg\b.*?</svg>", svg, html, flags=re.S)
    return re.sub(r'src="data:image/jpeg;base64,([^"]+)"', jpg, html)


# ------------------------------------------------------------------ 글자
def inline(n: Node | str) -> str:
    if isinstance(n, str):
        return re.sub(r"\s+", " ", n)
    if n.tag == "br":
        return " "
    if "pill" in n.cls:
        t = text(n).strip()
        return f"{EMOJI.get(t, '')} {t}"
    inner = "".join(inline(k) for k in n.kids)
    if n.tag == "b" and inner.strip():
        return f"**{inner.strip()}**"
    if n.tag == "small":
        return f" · `{inner.strip()}`"
    if n.tag == "img":
        return f"![]({n.attrs.get('src', '')})"
    return inner


def text(n: Node | str) -> str:
    return n if isinstance(n, str) else "".join(text(k) for k in n.kids)


def para(n: Node) -> str:
    return re.sub(r" {2,}", " ", inline(n)).strip()


def cell(n: Node) -> str:
    return para(n).replace("|", "\\|")


def table(div: Node) -> str:
    cap = div.find(cls="tcap")
    rows = div.find("tr")
    out = [para(cap[0]) if cap else "", ""]
    head = [cell(c) for c in rows[0].find("th")]
    out.append("| " + " | ".join(head) + " |")
    out.append("|" + "|".join(":---" for _ in head) + "|")
    for r in rows[1:]:
        out.append("| " + " | ".join(cell(c) for c in r.find("td")) + " |")
    return "\n".join(out)


def figure(f: Node) -> tuple[str, str]:
    img = f.find("img")[0].attrs["src"]
    cap = para(f.find("figcaption")[0])
    return img, cap


def figures_grid(div: Node, cols: int) -> str:
    figs = [figure(f) for f in div.find("figure")]
    out = []
    for i in range(0, len(figs), cols):
        row = figs[i : i + cols]
        w = 100 // cols - 1
        out.append("<p>" + " ".join(f'<img src="{s}" width="{w}%">' for s, _ in row) + "</p>")
        out.append("\n".join(f"<sub>{c}</sub><br>" for _, c in row))
        out.append("")
    return "\n".join(out)


def card(a: Node) -> str:
    key = text(a.find(cls="key")[0]).strip()
    nm = text(a.find(cls="nm")[0]).strip()
    pill = text(a.find(cls="pill")[0]).strip()
    tag = text(a.find(cls="tag")[0]).strip()
    dt = text(a.find(cls="dt")[0]).strip()
    ps = [para(p) for p in a.find("p")]
    fig = para(a.find(cls="fig")[0])
    return "\n".join(
        [
            f"#### {EMOJI.get(pill, '')} {key} — {nm}",
            "",
            f"`{pill}` · {tag} · {dt}",
            "",
            *[p + "\n" for p in ps],
            f"> **{fig}**",
            "",
        ]
    )


# ------------------------------------------------------------------ 블록
def block(n: Node | str, out: list[str]) -> None:
    if isinstance(n, str):
        t = n.strip()
        if t:
            out.append(t)
        return
    c = n.cls
    if n.tag == "section" and "cover" in c:
        cover(n, out)
        return
    if n.tag == "h2":
        num = n.find("span")
        t = text(n).strip()
        if num:
            t = f"{text(num[0]).strip()}. " + t[len(text(num[0]).strip()) :].strip()
        out += [f"## {t}", ""]
    elif n.tag in ("h3", "h4"):
        out += [f"{'#' * int(n.tag[1])} {para(n)}", ""]
    elif n.tag == "p":
        out += [para(n), ""]
    elif n.tag == "ul":
        out += [f"- {para(li)}" for li in n.find("li")] + [""]
    elif n.tag == "figure":
        s, cap = figure(n)
        out += [f"![{cap.replace('**', '')}]({s})", f"<sub>{cap}</sub>", ""]
    elif n.tag == "div" and "tbl" in c:
        out += [table(n), ""]
    elif n.tag == "div" and ("two" in c or "three" in c):
        out += [figures_grid(n, 2 if "two" in c else 3)]
    elif n.tag == "div" and "legend" in c:
        out += [" · ".join(f"{EMOJI[v[0]]} {v[0]}" for v in VERDICTS.values()), ""]
    elif n.tag == "div" and "famhead" in c:
        title = text(n.find("h3")[0]).strip()
        cnt = text(n.find(cls="famn")[0]).strip()
        chips = " · ".join(
            f"{inline(p).strip()} {text(k).strip()}"
            for p, k in zip(n.find(cls="pill"), n.find(cls="n"), strict=False)
        )
        out += [f"### {title} ({cnt})", "", chips, "", para(n.find("p")[0]), ""]
    elif n.tag == "div" and "fam" in c:
        for k in n.kids:
            if isinstance(k, Node) and "famhead" in k.cls:
                block(k, out)
        cards = n.find("article")
        out += [f"<details><summary><b>연구 카드 {len(cards)}장 펼쳐 보기</b></summary>", ""]
        out += [card(a) for a in cards]
        out += ["</details>", ""]
    elif n.tag == "dl":
        out += ["| 용어 | 뜻 |", "|:---|:---|"]
        for dt, dd in zip(n.find("dt"), n.find("dd"), strict=True):
            out.append(f"| **{cell(dt)}** | {cell(dd)} |")
        out.append("")
    else:
        for k in n.kids:
            block(k, out)


def cover(n: Node, out: list[str]) -> None:
    kicker = text(n.find(cls="kicker")[0]).strip()
    title = para(n.find("h1")[0])
    lede = para(n.find(cls="lede")[0])
    out += [
        '<div align="center">',
        "",
        f"<sub>{kicker}</sub>",
        "",
        f"# {title}",
        "",
        lede,
        "",
        "</div>",
        "",
    ]
    nums = n.find(cls="bignums")[0]
    vals = [
        (text(d.find("b")[0]).strip(), text(d.find("span")[0]).strip()) for d in nums.find("div")
    ]
    out += [
        "| " + " | ".join(f"**{v}**" for v, _ in vals) + " |",
        "|" + "|".join(":---:" for _ in vals) + "|",
        "| " + " | ".join(lab for _, lab in vals) + " |",
        "",
    ]
    grid = n.find(cls="covergrid")[0]
    out += ["| 항목 | 내용 |", "|:---|:---|"]
    for d in grid.find("div"):
        lab = text(d.find("span")[0]).strip()
        val = text(d).strip()[len(lab) :].strip()
        out.append(f"| {lab} | {val} |")
    out += ["", "---", ""]


TOC = """## 목차

| | | |
|:---|:---|:---|
| [요약](#요약) | [4. 연구 결과](#4-연구-결과) | [7. 오르빗 — 운영 대시보드와 UI](#7-오르빗--운영-대시보드와-ui) |
| [1. 문제 정의와 설계 원칙](#1-문제-정의와-설계-원칙) | [5. 전체 연구 카탈로그](#5-전체-연구-카탈로그) | [8. 품질 보증과 재현성](#8-품질-보증과-재현성) |
| [2. 데이터와 검증 기반](#2-데이터와-검증-기반) | [6. 운영 시스템과 위험 관리](#6-운영-시스템과-위험-관리) | [9. 한계와 향후 과제](#9-한계와-향후-과제) |
| [3. 연구 방법론](#3-연구-방법론) | | [용어](#용어) |

---
"""


def main() -> int:
    html = extract_assets(br.build())
    t = Tree()
    t.feed(html)
    out: list[str] = []
    for k in t.root.kids:
        block(k, out)
    md = "\n".join(out)
    md = md.replace("\n---\n\n## 요약", "\n---\n\n" + TOC + "\n## 요약", 1)
    md = md.replace(
        "왼쪽 띠의 색이 결론이다.", "제목 앞 색 표시가 결론이고, 카드는 부류마다 접혀 있다."
    )
    md = re.sub(r"\n{3,}", "\n\n", md).strip() + "\n"
    (ROOT / "README.md").write_text(md, encoding="utf-8")
    print("README.md", len(md.splitlines()), "줄 ·", len(list(ASSET_DIR.glob("*"))), "개 파일")
    return 0


if __name__ == "__main__":
    sys.exit(main())
