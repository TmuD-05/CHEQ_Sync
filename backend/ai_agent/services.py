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
        self.uri_pack = {}
        self.memory.setup()
        self.SYSTEM_PROMPT = """You are a flight booking assistant. When showing flight options:
                1. Keep responses concise and scannable
                2. Only show essential info: price, duration, stops, airline
                3. Skip detailed amenities unless user asks
                4. Format as a simple numbered list first, then 1-2 line summary
                5. Don't use markdown (no ##, **bold**, etc) unless user asks
                6. Provide the user with a link http://127.0.0.1:8000/confirmation_server/ where the user can confirm the flight bookings and get additional infromation about the flights

                Example format:
                Top 3 cheapest flights YVR to NRT:
                1. WestJet via YYC - $1,583 | 13h 40m (1 stop)
                2. ANA Direct - $1,742 | 10h 10m
                3. Air Canada Direct - $1,788 | 9h 45m

                Best value: WestJet saves money but adds connection time.
                Click this link http://127.0.0.1:8000/confirmation_server/  to confirm the flight  and add your information"""
        self.tools = [
            {
                    "name": "search_flights",
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
                                "description":" a date that should be formatted as YYYY-MM-DD. e.g. 2026-05-19",
                            },
                            "return_date": {
                                "type": "string",
                                "description": " a date that should be formatted as YYYY-MM-DD. e.g. 2026-05-19",
                            },
                            "type":{
                                "type": "integer",
                                "description": "Is it a round trip or one way eg if round trip then 1 (default), 2 for One way and 3 for Multi-city"
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
                model_kwargs={"system": self.SYSTEM_PROMPT}
            ).bind_tools(self.tools)

        while True:

            response = llm.invoke(messages)
            messages.append(response)

            if response.tool_calls:
                for tool_call in response.tool_calls:
                    if tool_call in response.tool_calls:
                        params = tool_call["args"]
                        result = self.execute_cheq_flow(params,session_id)
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

    def execute_cheq_flow(self, params,session_id="default"):
        try:

            response = httpx.post(
                'http://127.0.0.1:8000/resource_server/execute_process_with_confirmation/',
                json= params,
                timeout=40.0
            )

            if response.status_code != 202:
                return f"Error: Failed to trigger process {params}"

            flights_uri = response.json()
            self.uri_pack = flights_uri
            # confirm_response = httpx.post(
            #     uri_pack['confirmation_uri'],
            #     json={"resource_uri": uri_pack['resource_uri']},
            #     timeout=10.0
            # )
            #
            # if confirm_response.status_code != 200:
            #     return f"Error: Failed to trigger confirmation for process {params}"
            #
            # result_uri = uri_pack['result_uri']
            # result = self.poll_for_result(result_uri, params)

            return flights_uri["flights"]

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

