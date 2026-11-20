"""Convert the job Overview HTML into clean Markdown-style text (standard library only).

Keeps headings, bold text, bullet lists and links the way they look on the page,
unlike Ashby's descriptionPlain which upper-cases headings and adds blank lines.
"""
import re
from html.parser import HTMLParser

_BLOCK = {"p", "div", "section", "ul", "ol", "table", "tr", "blockquote"}
_HEADINGS = {"h1": "# ", "h2": "## ", "h3": "### ", "h4": "#### ", "h5": "##### ", "h6": "###### "}


class _Converter(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out = []
        self.lists = []      # stack of ["ul"|"ol", counter]
        self.href = None
        self.in_heading = False
        self.bold_open = False

    def _newline(self, n=1):
        text = "".join(self.out)
        if re.search(r"(^|\n) *(-|\d+\.) $", text):   # right after a bullet marker (<li><p>)
            return
        have = len(text) - len(text.rstrip("\n"))
        if text and have < n:
            self.out.append("\n" * (n - have))

    def handle_starttag(self, tag, attrs):
        if tag in _HEADINGS:
            self._newline(2)
            self.out.append(_HEADINGS[tag])
            self.in_heading = True
        elif tag in _BLOCK:
            self._newline(2 if not self.lists else 1)
            if tag in ("ul", "ol"):
                self.lists.append([tag, 0])
        elif tag == "li":
            self._newline(1)
            indent = "  " * max(len(self.lists) - 1, 0)
            if self.lists and self.lists[-1][0] == "ol":
                self.lists[-1][1] += 1
                self.out.append(f"{indent}{self.lists[-1][1]}. ")
            else:
                self.out.append(f"{indent}- ")
        elif tag == "br":
            self.out.append("\n")
        elif tag in ("strong", "b") and not self.in_heading:
            self.out.append("**")
            self.bold_open = True
        elif tag in ("em", "i"):
            self.out.append("_")
        elif tag == "a":
            self.href = dict(attrs).get("href")

    def handle_endtag(self, tag):
        if tag in _HEADINGS:
            self.in_heading = False
        if tag in _HEADINGS or tag in _BLOCK:
            if tag in ("ul", "ol") and self.lists:
                self.lists.pop()
            self._newline(2 if not self.lists else 1)
        elif tag in ("strong", "b") and not self.in_heading:
            # "<strong>Text </strong>and" -> "**Text** and"
            last = self.out[-1] if self.out else ""
            if last.endswith(" "):
                self.out[-1] = last.rstrip()
                self.out.append("** ")
            else:
                self.out.append("**")
        elif tag in ("em", "i"):
            self.out.append("_")
        elif tag == "a":
            if self.href and self.href.startswith("http"):
                self.out.append(f" ({self.href})")
            self.href = None

    def handle_data(self, data):
        data = re.sub(r"\s+", " ", data)
        if "".join(self.out[-1:]).endswith(("\n", "# ", "- ", ". ")) or not self.out:
            data = data.lstrip()
        if self.bold_open and data.startswith(" "):
            # "<strong> Text</strong>" -> " **Text**"
            self.out[-1] = " **"
            data = data.lstrip()
        self.bold_open = False
        self.out.append(data)


def html_to_text(html):
    if not html:
        return ""
    conv = _Converter()
    conv.feed(html)
    conv.close()
    text = "".join(conv.out).replace("\xa0", " ")
    text = re.sub(r"\*\*[ \t]*\*\*", "", text)        # empty bold
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n[ \t]+(?![-\d ])", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()
