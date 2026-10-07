from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_classic.agents import create_tool_calling_agent, AgentExecutor
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder

from app.agent.mcp_client import MCPToolClient
from app.agent.mcp_tool_adapter import to_langchain_tools

# Each /ask is about one database; the agent has no use for finding others.
AGENT_EXCLUDED_TOOLS = {"list_databases"}


class AgentFactory:
    """
    Assembles the LangChain agent from the LLM and the tools an MCP
    session advertised.
    """

    def __init__(
        self,
        mcp_client: MCPToolClient,
        google_api_key: str,
        model: str = "gemini-3.5-flash-lite",
    ) -> None:
        self.mcp_client = mcp_client
        self.google_api_key = google_api_key
        self.model = model

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

        all_tools = [
            tool for tool in to_langchain_tools(self.mcp_client)
            if tool.name not in AGENT_EXCLUDED_TOOLS
        ]

        system_prompt = self._build_system_prompt(database_id, all_tools)

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
    def _build_system_prompt(database_id: str, tools: list) -> str:
        tool_list = "\n".join(f"{i}. {t.name} - {t.description}" for i, t in enumerate(tools, 1))
        # Descriptions come from the MCP server; the result is a prompt template.
        tool_list = tool_list.replace("{", "{{").replace("}", "}}")
        return f"""You are an expert SQL assistant helping users query a PostgreSQL database using natural language.

Database ID: {database_id}

You have access to the following tools (always pass database_id="{database_id}" to those that take it):
{tool_list}

Your workflow:
1. If you don't know which tables exist, call list_schemas first
2. If you need to understand a table's structure, call describe_table
3. If you're unsure about SQL semantics (joins, subqueries, window functions, etc.), call sql_reference_lookup BEFORE writing SQL
4. Optionally call sample_rows to understand data patterns
5. Finally, call run_sql with the SQL query

Writes (INSERT/UPDATE/DELETE/CREATE) are never executed directly: run_sql returns CONFIRMATION_REQUIRED and the user is shown a "Run anyway" button. When that happens, explain in plain words what the statement will do and stop; do not retry or rephrase it.

Never end your turn by saying you will run a query: call run_sql in the same turn, then answer from its result.

Always:
- Ask clarifying questions if the user's request is ambiguous
- Explain your SQL query before executing it
- Ground your explanations in the retrieved textbook passages when relevant
- Handle errors gracefully and suggest corrections

Earlier turns of this conversation may be included; treat follow-up questions ("now only those from Pune", "sort that by price") as refinements of the previous query.

Start by understanding what the user wants to do."""
