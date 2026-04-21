import os
eval("1+1")
os.system("echo hi")

from langchain_core.tools import tool


@tool
def t() -> str:
    return "x"


__skill_tools__ = [t]
