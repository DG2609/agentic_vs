from langchain_core.tools import tool

@tool
def handoff_to_coder(instructions: str) -> str:
    """Hand off execution to the Coder agent to implement the plan.
    
    Args:
        instructions: Detailed instructions, architecture, and the plan for the coder to execute.
    """
    return f"Handing off to Coder Agent with instructions: {instructions}"


@tool
def handoff_to_planner(reason: str) -> str:
    """Hand off execution back to the Planner agent for re-evaluation or new architectural decisions.
    
    Args:
        reason: Why the planner needs to step in (e.g., plan is flawed, hit a roadblock, need more research).
    """
    return f"Handing off to Planner Agent because: {reason}"
