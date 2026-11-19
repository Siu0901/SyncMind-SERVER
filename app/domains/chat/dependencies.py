from typing import Annotated

from fastapi import Depends

from app.domains.chat.agent import ChatAgent
from app.domains.knowledge.dependencies import RetrievalServiceDep
from app.domains.workspace.dependencies import CurrentWorkspaceAccessDep
from app.infra.llm.factory import get_llm
from app.infra.llm.port import LLMPort


LLMDep = Annotated[LLMPort, Depends(get_llm)]

CurrentChatAccessDep = CurrentWorkspaceAccessDep


def get_chat_agent(
    llm: LLMDep,
    retrieval: RetrievalServiceDep,
) -> ChatAgent:
    return ChatAgent(llm=llm, retrieval=retrieval)


ChatAgentDep = Annotated[ChatAgent, Depends(get_chat_agent)]
