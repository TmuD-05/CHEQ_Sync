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
        self.db_conn.execute(
            "CREATE TABLE IF NOT EXISTS uri_pack (session_id TEXT PRIMARY KEY, data TEXT)"
        )
        self.db_conn.commit()
        self.uri_pack = {}
        self.memory.setup()
        self.SYSTEM_PROMPT = """You are a flight booking assistant. When showing flight options:
                1. Keep responses concise and scannable
                2. Only show essential info: price, duration, stops, airline
                3. Skip detailed amenities unless user asks
                4. Format as a simple numbered list first, then 1-2 line summary
                5. Don't use markdown (no ##, **bold**, etc) unless user asks
               
                Example format:
                Top 3 cheapest flights YVR to NRT:
                1. WestJet via YYC - $1,583 | 13h 40m (1 stop)
                2. ANA Direct - $1,742 | 10h 10m
                3. Air Canada Direct - $1,788 | 9h 45m
                
                Best value: WestJet saves money but adds connection time.
                When poll_booking_result returns an ACCEPT result, respond with a clear confirmation message like:
                'Your Air Canada AC3 flight has been confirmed and booked successfully. Have a great trip!'
                Never give a generic response when a booking result is available
                
   """
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

            },
            {
                "name": "send_confirmation_link",
                "description": "Call this immediately after the user makes a selection. Sends the user the confirmation link and waits for them to navigate there.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "selected_flight": {
                            "type": "string",
                            "description": "Brief description of the selected flight e.g. 'WestJet WS110 - $1,583'"
                        }
                    },
                    "required": ["selected_flight"]
                }
            },
            {
                "name": "poll_booking_result",
                "description": "Call this ONLY after send_confirmation_link has been called and the user indicates they have completed the confirmation page.",
                "input_schema": {
                    "type": "object",
                    "properties": {},
                    "required": []
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
                model="claude-sonnet-4-6",
                anthropic_api_key = self.api_key,
                model_kwargs={"system": self.SYSTEM_PROMPT}
            ).bind_tools(self.tools)

        while True:

            response = llm.invoke(messages)
            messages.append(response)

            if response.tool_calls:
                for tool_call in response.tool_calls:

                        tool_name = tool_call["name"]
                        params = tool_call["args"]

                        if tool_name == "search_flights":
                            result = self.execute_cheq_flow(params,session_id)
                        elif tool_name == "send_confirmation_link":
                            result = "http://127.0.0.1:8000/confirmation_server/ — please confirm your booking there."
                        elif tool_name == "poll_booking_result":

                            result = self.poll_for_result(
                                session_id,
                                max_attempts=60,
                                interval=5
                            )
                        else:
                            result = f"Unknown tool: {tool_name}"
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
                return f"Error: Failed to trigger process"

            flights_uri = response.json()
            self.uri_pack = {
                "confirmation_uri" : flights_uri["confirmation_uri"],
                "resource_uri" : flights_uri["resource_uri"],
                "result_uri" : flights_uri["result_uri"],
            }

            self._save_uri_pack(session_id)
            return flights_uri["flights"]

        except Exception as e:
            return f"Error executing CHEQ flow: {str(e)}"

    def poll_for_result(self,session_id="default",max_attempts=60, interval=5 ):
        self._load_uri_pack(session_id)
        if not self.uri_pack:
            return "Error: No booking context found. Please search for flights first."

        confirm_response = httpx.post(
            self.uri_pack["confirmation_uri"],
            json = {"resource_uri": self.uri_pack["resource_uri"]},
            timeout=10.0
        )

        if confirm_response.status_code != 200:
            return f"Error: Failed to trigger confirmation for process"
        for attempt in range(max_attempts):
            try:
                response = httpx.get(self.uri_pack["result_uri"], timeout=5.0)
                result = response.json()

                if result and len(result) > 0:
                    status = result[0]['confirmation_status']

                    if status != 'PENDING':
                        if status == 'ACCEPT':
                            return f" Flight was approved and executed successfully!"
                        else:
                            return f" Flight was rejected."

                time.sleep(interval)

            except Exception as e:
                print(f"--- Network Attempt Failed: {e} ---")
                time.sleep(interval)
                continue

        return f"Timeout: No confirmation received  {max_attempts * interval} seconds."

    def clear_memory(self, session_id="default"):
        thread_id = f"session_{session_id}"
        config = {"configurable": {"thread_id": thread_id}}

        self.memory.put(
            config,
            {"messages": []},
            {},
            {}
        )

    def _save_uri_pack(self, session_id):
        self.db_conn.execute(
            "INSERT OR REPLACE INTO uri_pack (session_id, data) VALUES (?, ?)",
            (session_id, json.dumps(self.uri_pack))
        )
        self.db_conn.commit()

    def _load_uri_pack(self, session_id):
        self.db_conn.execute(
            "CREATE TABLE IF NOT EXISTS uri_pack (session_id TEXT PRIMARY KEY, data TEXT)"
        )
        row = self.db_conn.execute(
            "SELECT data FROM uri_pack WHERE session_id = ?", (session_id,)
        ).fetchone()
        if row:
            self.uri_pack = json.loads(row[0])