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

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

class AgentService:
    def __init__(self):
        self.api_key=os.getenv("ANTHROPIC_API_KEY")
        db_path = os.path.join(BASE_DIR, "cheq_memory.db")
        self.db_conn = sqlite3.connect(db_path, check_same_thread=False)

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

                Booking & Verification Rules:
                - When the user selects a flight (e.g. '1', '2', or airline name), immediately call `send_confirmation_link`.
                - When the user reports back after visiting the confirmation page (or says 'I have accepted and authorized the booking.' or 'I have rejected the booking.'), you MUST ALWAYS immediately call the `poll_booking_result` tool to check the cryptographic confirmation outcome.
                - When poll_booking_result returns an ACCEPT result (e.g. 'Flight was approved and executed successfully!'), announce a joyful and clear confirmation message detailing their specific booked flight (airline, flight number, price, departure/arrival times) and wish them a great trip!
                - When poll_booking_result returns a REJECT result (e.g. 'Flight was rejected.'), acknowledge that the booking was cancelled/declined, and offer to help them book one of the other options from their search or adjust search criteria.
                - Never ask the user to choose a flight again if they have already made a choice or are reporting back from the confirmation page.
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
                "description": "Call this immediately after the user makes a flight selection. Sends the user the confirmation link and waits for them to navigate there.",
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
                "description": "Call this immediately whenever the user indicates they have completed, authorized, accepted, or rejected the booking on the confirmation page. Returns the cryptographic confirmation result.",
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
                            selected_flight = params.get("selected_flight")
                            self._load_uri_pack(session_id)
                            if self.uri_pack and "resource_uri" in self.uri_pack:
                                try:
                                    resp = httpx.post(
                                        f"{self.uri_pack['resource_uri'].rstrip('/')}/select_flight/",
                                        json={"selected_flight": selected_flight},
                                        timeout=10.0
                                    )
                                    if resp.status_code == 409:
                                        result = (
                                            "A flight booking for this session has already been authorized and finalized. "
                                            "To book another flight, please start a new flight search."
                                        )
                                    elif resp.status_code == 200:
                                        try:
                                            resp_data = resp.json()
                                            if isinstance(resp_data, dict) and "resource_uri" in resp_data:
                                                self.uri_pack["resource_uri"] = resp_data["resource_uri"]
                                                if "result_uri" in resp_data:
                                                    self.uri_pack["result_uri"] = resp_data["result_uri"]
                                                self._save_uri_pack(session_id)
                                        except Exception:
                                            pass
                                        react_url = f"/?resource_uri={self.uri_pack['resource_uri']}"
                                        result = f"[Please click here to confirm your booking]({react_url})"
                                    else:
                                        result = f"Error saving flight selection: {resp.text}"
                                except Exception as e:
                                    print(f"Error saving flight selection to resource server: {e}")
                                    react_url = f"/?resource_uri={self.uri_pack['resource_uri']}"
                                    result = f"[Please click here to return]({react_url})"
                            else:
                                result = f"Could not find confirmation URL. Please try again."
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
                now_utc = datetime.now(timezone.utc)
                checkpoint_id = f"{now_utc.strftime('%Y%m%d%H%M%S%f')}_{uuid.uuid4().hex}"
                checkpoint_data = {
                    "v": 1,
                    "id": checkpoint_id,
                    "ts": now_utc.isoformat(),
                    "channel_values": {"messages": messages},
                    "channel_versions": {},
                    "versions_seen": {},
                    "pending_sends": [],
                }

                # Clean up any older checkpoints for this thread so there is no accidental branching or rewinding
                cursor = self.db_conn.cursor()
                cursor.execute("DELETE FROM checkpoints WHERE thread_id = ?", (thread_id,))
                cursor.execute("DELETE FROM writes WHERE thread_id = ?", (thread_id,))
                self.db_conn.commit()

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

    def poll_for_result(self, session_id="default", max_attempts=60, interval=5):
        self._load_uri_pack(session_id)
        if not self.uri_pack:
            return "Error: No booking context found. Please search for flights first."

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
        config = {"configurable":
                      {"thread_id": thread_id, "checkpoint_ns": ""}
                  }

        checkpoint_data = {
            "v": 1,
            "id": str(uuid.uuid4()),
            "ts": datetime.now(timezone.utc).isoformat(),
            "channel_values": {"messages": []},
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
        try:
            self.db_conn.execute("DELETE FROM uri_pack WHERE session_id = ?", (session_id,))
            self.db_conn.commit()
        except Exception:
            pass

    def get_all_chats(self):
        cursor = self.db_conn.cursor()
        cursor.execute("SELECT DISTINCT thread_id FROM checkpoints")
        threads = cursor.fetchall()
        
        chat_list = []
        for t in threads:
            thread_id = t[0]
            # Ensure it is a user session
            if not thread_id.startswith("session_"):
                continue
            
            # Remove only the first "session_" prefix to get the session_id
            session_id = thread_id[len("session_"):]

            config = {"configurable": {"thread_id": thread_id, "checkpoint_ns": ""}}
            checkpoint = self.memory.get_tuple(config)
            
            if checkpoint and checkpoint.checkpoint:
                messages = checkpoint.checkpoint.get("channel_values", {}).get("messages", [])
                ts = checkpoint.checkpoint.get("ts", "")
                
                title = "New Chat"
                # the creation of the tile we should have ai summaries that chat andd genrate an appropriate title
                #not just extracting the first human message anf having that as the title for that conversation line 300 -313
                for msg in messages:
                    if msg.__class__.__name__ == "HumanMessage" or getattr(msg, 'type', '') == 'human':
                        title = getattr(msg, 'content', str(msg))
                        if len(title) > 40:
                            title = title[:40] + "..."
                        break
                
                chat_list.append({
                    "session_id": session_id,
                    "title": title,
                    "updated_at": ts
                })
        
        chat_list.sort(key=lambda x: x["updated_at"], reverse=True)
        return chat_list

    def get_chat_messages(self, session_id):
        # We always prefix session_id with "session_" to reconstruct the DB thread_id
        thread_id = f"session_{session_id}"
        config = {"configurable": {"thread_id": thread_id, "checkpoint_ns": ""}}
        checkpoint = self.memory.get_tuple(config)
        
        frontend_messages = []
        frontend_messages.append({
            "id": 1,
            "message": "Hi! How can I help you today?",
            "type": "Bot"
        })
        
        if checkpoint and checkpoint.checkpoint:
            messages = checkpoint.checkpoint.get("channel_values", {}).get("messages", [])
            for idx, msg in enumerate(messages):
                msg_type = msg.__class__.__name__
                
                if msg_type == "HumanMessage" or getattr(msg, 'type', '') == 'human':
                    content = getattr(msg, 'content', '')
                    if content:
                        frontend_messages.append({
                            "id": idx + 2,
                            "message": content,
                            "type": "User"
                        })
                elif msg_type == "AIMessage" or getattr(msg, 'type', '') == 'ai':
                    content = getattr(msg, 'content', '')
                    if isinstance(content, list):
                        text_blocks = [block.get('text', '') for block in content if isinstance(block, dict) and block.get('type') == 'text']
                        content = "\n".join(text_blocks)
                    if content:
                        frontend_messages.append({
                            "id": idx + 2,
                            "message": content,
                            "type": "Bot"
                        })
        return frontend_messages

    def delete_chat(self, session_id):
        thread_id = f"session_{session_id}"
        cursor = self.db_conn.cursor()
        cursor.execute("DELETE FROM checkpoints WHERE thread_id = ?", (thread_id,))
        cursor.execute("DELETE FROM writes WHERE thread_id = ?", (thread_id,))
        cursor.execute("DELETE FROM uri_pack WHERE session_id = ?", (session_id,))
        self.db_conn.commit()

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