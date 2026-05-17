import json
import sqlite3
import uuid
from datetime import datetime, timezone
import anthropic
import httpx
import time
import os
from langgraph.checkpoint.sqlite import SqliteSaver
from langchain_core.messages import HumanMessage,  ToolMessage
from langchain_anthropic import ChatAnthropic

class AgentService:
    def __init__(self):
        self.api_key=os.getenv("ANTHROPIC_API_KEY")
        self.db_conn = sqlite3.connect("cheq_memory.db", check_same_thread=False)

        self.memory = SqliteSaver(self.db_conn)

        self.memory.setup()

        self.tools = [
            {
                    "name": "execute_process",
                    "description": "Search for flights and get confirmation options",
                    "input_schema": {
                        "type": "object",
                        "properties": {
                            "origin": {
                                "type": "string",
                                "description": "A three letter airport code eg YVR, HRE, LAX "
                            },
                            "destination": {
                                "type": "string",
                                "description": "A three letter airport code eg YVR, LAX "
                            },
                            "outbound_date":{
                                "type": "string",
                                "description":" a date that can be dd-mm-yyyy or mm-dd-yyyy",
                            },
                            "return_date": {
                                "type": "string",
                                "description": " a date that can be dd-mm-yyyy or mm-dd-yyyy",
                            },
                            "type":{
                                "type": "string",
                                "description": "Is it a round trip or one way"
                            }
                        },
                        "required": ["origin","destination","outbound_date","return_date","type"]
                    }

            }
        ]
    def chat(self, user_message, session_id="default"):

        thread_id = f"session_{session_id}"
        config = {"configurable":
                      {"thread_id": thread_id, "checkpoint_ns": ""}
                  }
        checkpoint = self.memory.get_tuple(config)
        if checkpoint and checkpoint.checkpoint:
            messages = checkpoint.checkpoint.get("channel_values", {}).get("messages", [])
        else:
            messages = []

        messages.append(HumanMessage(content=user_message))
        llm = ChatAnthropic(
                model="claude-sonnet-4-20250514",
                anthropic_api_key = self.api_key,
            ).bind_tools(self.tools)

        while True:

            response = llm.invoke(messages)
            messages.append(response)

            if response.tool_calls:
                for tool_call in response.tool_calls:
                    if tool_call in response.tool_calls:
                        params = tool_call["args"]
                        result = self.execute_cheq_flow(params)
                        messages.append(ToolMessage(
                            tool_call_id = tool_call["id"],
                            content = str(result)
                        ))
                continue

            else:
                final_response = response.content
                checkpoint_data = {
                    "v": 1,
                    "id": str(uuid.uuid4()),
                    "ts": datetime.now(timezone.utc).isoformat(),
                    "channel_values": {"messages": messages},
                    "channel_versions": {},
                    "versions_seen": {},
                    "pending_sends": [],
                }

                self.memory.put(
                    config,
                    checkpoint_data,
                    {},
                    {}
                )
                return final_response

    def execute_cheq_flow(self, params):
        try:

            response = httpx.post(
                'http://127.0.0.1:8000/resource_server/execute_process_with_confirmation/',
                json={"parameters": params},
                timeout=10.0
            )

            if response.status_code != 202:
                return f"Error: Failed to trigger process {params}"

            uri_pack = response.json()

            confirm_response = httpx.post(
                uri_pack['confirmation_uri'],
                json={"resource_uri": uri_pack['resource_uri']},
                timeout=10.0
            )

            if confirm_response.status_code != 200:
                return f"Error: Failed to trigger confirmation for process {params}"

            result_uri = uri_pack['result_uri']
            result = self.poll_for_result(result_uri, params)

            return result

        except Exception as e:
            return f"Error executing CHEQ flow: {str(e)}"

    def poll_for_result(self, result_uri, process_id,max_attempts=60, interval=5):
        for attempt in range(max_attempts):
            try:
                response = httpx.get(result_uri, timeout=5.0)
                result = response.json()

                if result and len(result) > 0:
                    status = result[0]['confirmation_status']

                    if status != 'PENDING':
                        if status == 'ACCEPT':
                            return f" Process {process_id} was approved and executed successfully!"
                        else:
                            return f" Process {process_id} was rejected."

                time.sleep(interval)

            except Exception as e:
                time.sleep(interval)
                continue

        return f"Timeout: No confirmation received for process {process_id} within {max_attempts * interval} seconds."

    def clear_memory(self, session_id="default"):
        thread_id = f"session_{session_id}"
        config = {"configurable": {"thread_id": thread_id}}

        self.memory.put(
            config,
            {"messages": []},
            {},
            {}
        )
