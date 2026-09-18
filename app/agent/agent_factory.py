from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_classic.agents import create_tool_calling_agent, AgentExecutor
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder

from app.agent.mcp_tool_adapter import MCPToolAdapter
from app.agent.sql_reference_lookup_tool import (
    SqlReferenceLookupTool,
    create_sql_reference_lookup_tool,
)
from app.agent.tool_service_adapter import ToolServiceAdapter
from app.services.rag_service import RAGService


class AgentFactory:
    """
    Assembles the LangChain agent with database tools, RAG retrieval, and LLM.
    """

    def __init__(
        self,
        tool_client: ToolServiceAdapter,
        rag_service: RAGService,
        google_api_key: str,
        model: str = "gemini-3.5-flash-lite",
    ) -> None:
        self.tool_client = tool_client
        self.rag_service = rag_service
        self.google_api_key = google_api_key
        self.model = model
        self.rag_tool: SqlReferenceLookupTool | None = None

    async def create_agent_executor(
        self,
        database_id: str,
        max_iterations: int = 10,
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
            model=self.model,
            temperature=0,
            google_api_key=self.google_api_key,
        )

        adapter = MCPToolAdapter(self.tool_client)
        db_tools = adapter.create_tools()

        self.rag_tool = create_sql_reference_lookup_tool(self.rag_service)

        all_tools = db_tools + [self.rag_tool.as_structured_tool()]

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
        return f"""You are an expert SQL assistant helping users query a PostgreSQL database using natural language.

Database ID: {database_id}

You have access to the following tools (always pass database_id="{database_id}"):
1. list_schemas - List all tables in the database
2. describe_table - Get column information for a specific table
3. sample_rows - See sample data from a table
4. run_sql - Execute a single SQL statement: SELECT, INSERT, UPDATE, DELETE, or CREATE TABLE/INDEX/VIEW (writes need write or owner access). Reference tables by bare name only (e.g. "orders"), never schema-qualified.
5. sql_reference_lookup - Look up SQL/DBMS concepts in the textbook

Your workflow:
1. If you don't know which tables exist, call list_schemas first
2. If you need to understand a table's structure, call describe_table
3. If you're unsure about SQL semantics (joins, subqueries, window functions, etc.), call sql_reference_lookup BEFORE writing SQL
4. Optionally call sample_rows to understand data patterns
5. Finally, call run_sql with the SQL query

Writes (INSERT/UPDATE/DELETE/CREATE) are never executed directly: run_sql returns CONFIRMATION_REQUIRED and the user is shown a "Run anyway" button. When that happens, explain in plain words what the statement will do and stop; do not retry or rephrase it.

Always:
- Ask clarifying questions if the user's request is ambiguous
- Explain your SQL query before executing it
- Ground your explanations in the retrieved textbook passages when relevant
- Handle errors gracefully and suggest corrections

Earlier turns of this conversation may be included; treat follow-up questions ("now only those from Pune", "sort that by price") as refinements of the previous query.

Start by understanding what the user wants to do."""
