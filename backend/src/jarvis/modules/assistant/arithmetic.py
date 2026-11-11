"""Calculadora decimal. Somente literais, parênteses e operadores aritméticos."""

import ast
import operator
import re
from decimal import Decimal, DecimalException, localcontext

from jarvis.modules.assistant.intent import fold
from jarvis.modules.assistant.numbers_pt import SPOKEN, integer
from jarvis.modules.calendar.periods import command_text

OPERATIONS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.Pow: operator.pow,
}
AMOUNT = r"\d+(?:[.,]\d+)*"


def arithmetic(text: str) -> str | None:
    normal = fold(command_text(text))
    expr = re.sub(
        r"^(?:calcule|calcula|calcular|quanto (?:e|da|fica|sao)|quanto que (?:e|da))\s+", "", normal
    )
    if expr == normal and not re.match(r"^[\d(+-]", expr):
        return None
    if expr == normal and re.fullmatch(r"\d{1,4}(?:[-/]\d{1,2}){1,2}", expr):
        return None
    expr = expr.replace("por cento", "%")
    try:
        expr = re.sub(r"\b(?:" + SPOKEN + r")\b", lambda m: str(integer(m[0])), expr)
    except ValueError:
        return None
    expr = re.sub(r"\s*reais\b|r\$\s*", "", expr)
    expr = re.sub(r"\b(?:dividid[oa] por|dividid[oa] entre)\b", "/", expr)
    expr = re.sub(r"\b(?:multiplicad[oa] por|vezes)\b", "*", expr)
    expr = re.sub(r"\bmais\b", "+", expr)
    expr = re.sub(r"\bmenos\b", "-", expr)
    expr = re.sub(r"(?<=\d)\s*[x×]\s*(?=\d)", "*", expr).replace("÷", "/")
    expr = re.sub(
        rf"({AMOUNT})\s*(?:%|por cento)\s*(?:de|sobre)\s*({AMOUNT})", r"(\1/100*\2)", expr
    )
    if (
        len(expr) > 180
        or not re.fullmatch(r"[-+*()/\d.,\s^]+", expr)
        or not re.search(r"[-+/*^]", expr)
    ):
        return None

    def normalize(m):
        n = m[0]
        if re.fullmatch(r"[1-9]\d{0,2}(?:\.\d{3})+(?:,\d+)?", n):
            n = n.replace(".", "")
        elif not re.fullmatch(r"\d+(?:[,.]\d+)?", n):
            raise ValueError
        return n.replace(",", ".")

    def value(node) -> Decimal:
        if isinstance(node, ast.Constant) and type(node.value) in (int, float):
            return Decimal(ast.get_source_segment(expr, node) or "")
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd)):
            number = value(node.operand)
            return -number if isinstance(node.op, ast.USub) else number
        if not isinstance(node, ast.BinOp) or type(node.op) not in OPERATIONS:
            raise ValueError
        left, right = value(node.left), value(node.right)
        if isinstance(node.op, ast.Pow) and (right != right.to_integral_value() or abs(right) > 20):
            raise ValueError
        result = OPERATIONS[type(node.op)](left, right)
        if not result.is_finite() or (result and abs(result.adjusted()) > 60):
            raise ValueError
        return result

    try:
        expr = re.sub(AMOUNT, normalize, expr).replace("^", "**")
        with localcontext() as ctx:
            ctx.prec = 40
            result = value(ast.parse(expr, mode="eval").body).normalize()
            return "Resultado: " + format(result, "f").replace(".", ",") + "."
    except (SyntaxError, ValueError, DecimalException, OverflowError):
        return "Não consegui calcular essa expressão. Confira os operadores e o divisor."
