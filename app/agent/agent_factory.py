from langchain_core.tools import StructuredTool
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_classic.agents import create_tool_calling_agent, AgentExecutor
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder

from app.agent.mcp_tool_adapter import MCPToolAdapter
from app.agent.sql_reference_lookup_tool import create_sql_reference_lookup_tool
from app.mcp_server.client import MCPClientWrapper
from app.services.rag_service import RAGService
import os
from dotenv import load_dotenv

load_dotenv()


class AgentFactory:
    """
    Assembles the LangChain agent with MCP tools, RAG retrieval, and LLM.
    """

    def __init__(
        self,
        mcp_client: MCPClientWrapper,
        rag_service: RAGService,
        google_api_key: str,
    ) -> None:
        self.mcp_client = mcp_client
        self.rag_service = rag_service
        self.google_api_key = google_api_key

    async def create_agent_executor(
        self,
        database_id: str,
        max_iterations: int = 6,
    ) -> AgentExecutor:
        """
        Create an agent executor for a specific database.

        Args:
            database_id: The target database ID
            max_iterations: Maximum tool-call iterations before stopping

        Returns:
            An AgentExecutor ready to process user queries
        """
        llm = ChatGoogleGenerativeAI(
            model="gemini-3.6-flash",
            temperature=0,
            google_api_key=self.google_api_key,
        )

        adapter = MCPToolAdapter(self.mcp_client)
        mcp_tools = adapter.create_tools()

        rag_tool = create_sql_reference_lookup_tool(self.rag_service)

        all_tools = mcp_tools + [rag_tool]

        system_prompt = self._build_system_prompt(database_id)

        prompt = ChatPromptTemplate.from_messages(
            [
                ("system", system_prompt),
                MessagesPlaceholder(variable_name="chat_history", optional=True),
                ("human", "{input}"),
                MessagesPlaceholder(variable_name="agent_scratchpad"),
            ]
        )

        agent = create_tool_calling_agent(
            llm=llm,
            tools=all_tools,
            prompt=prompt,
        )

        executor = AgentExecutor(
            agent=agent,
            tools=all_tools,
            max_iterations=max_iterations,
            return_intermediate_steps=True,
            verbose=False,
        )

        return executor

    @staticmethod
    def _build_system_prompt(database_id: str) -> str:
        return f"""You are an expert SQL assistant helping users query a database using natural language.

Database ID: {database_id}

You have access to the following tools:
1. list_schemas - List all tables in the database
2. describe_table - Get column information for a specific table
3. sample_rows - See sample data from a table
4. run_sql - Execute SQL queries
5. sql_reference_lookup - Look up SQL/DBMS concepts in the textbook

Your workflow:
1. If you don't know which tables exist, call list_schemas first
2. If you need to understand a table's structure, call describe_table
3. If you're unsure about SQL semantics (joins, subqueries, window functions, etc.), call sql_reference_lookup BEFORE writing SQL
4. Optionally call sample_rows to understand data patterns
5. Finally, call run_sql with the SQL query

Always:
- Ask clarifying questions if the user's request is ambiguous
- Explain your SQL query before executing it
- Ground your explanations in the retrieved textbook passages when relevant
- Handle errors gracefully and suggest corrections

Start by understanding what the user wants to do."""