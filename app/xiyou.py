"""西柚 MCP 客户端：BSR 校验 + 销量补充（可选）。

凭证只从环境变量读取：
    XIYOU_MCP_URL    默认 https://mcp.xydc.com/mcp
    XIYOU_MCP_TOKEN  西柚后台生成的 Bearer token
未配置时所有方法返回 None，调用方自动跳过，不影响主流程。
"""
import json
import os
import urllib.error
import urllib.request
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
except Exception:
    pass


class MCPError(RuntimeError):
    pass


def available() -> bool:
    return bool(os.getenv("XIYOU_MCP_TOKEN"))


class XiyouClient:
    def __init__(self, url=None, token=None, timeout=60):
        self.url = (url or os.getenv("XIYOU_MCP_URL") or "https://mcp.xydc.com/mcp").strip()
        raw = (token if token is not None else os.getenv("XIYOU_MCP_TOKEN") or "").strip()
        if not raw:
            raise MCPError("缺少 XIYOU_MCP_TOKEN")
        self.token = raw if raw.lower().startswith("bearer ") else f"Bearer {raw}"
        self.timeout = timeout
        self.session_id = None
        self._connected = False
        self._tools = None

    # ---------- 传输 ----------
    def _post(self, payload):
        headers = {"Content-Type": "application/json",
                   "Accept": "application/json, text/event-stream",
                   "Authorization": self.token}
        if self.session_id:
            headers["Mcp-Session-Id"] = self.session_id
        req = urllib.request.Request(self.url, data=json.dumps(payload).encode(),
                                     headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                sid = resp.headers.get("Mcp-Session-Id")
                if sid:
                    self.session_id = sid
                return resp.read().decode("utf-8", "ignore")
        except urllib.error.HTTPError as e:
            raise MCPError(f"HTTP {e.code}: {e.read().decode('utf-8', 'ignore')[:200]}")
        except urllib.error.URLError as e:
            raise MCPError(f"连接失败: {e.reason}")

    @staticmethod
    def _parse(body):
        body = (body or "").strip()
        if not body:
            return []
        if body.startswith("{"):
            try:
                return [json.loads(body)]
            except json.JSONDecodeError:
                return []
        msgs = []
        for block in body.split("\n\n"):
            data = "\n".join(l[5:].strip() for l in block.splitlines() if l.startswith("data:"))
            if data:
                try:
                    msgs.append(json.loads(data))
                except json.JSONDecodeError:
                    pass
        return msgs

    def _rpc(self, method, params=None, notify=False):
        payload = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            payload["params"] = params
        if not notify:
            payload["id"] = 1
        msgs = self._parse(self._post(payload))
        for m in msgs:
            if m.get("error"):
                raise MCPError(f"{method}: {json.dumps(m['error'], ensure_ascii=False)[:200]}")
        return msgs

    def connect(self):
        if self._connected:
            return self
        self._rpc("initialize", {"protocolVersion": "2024-11-05", "capabilities": {},
                                 "clientInfo": {"name": "competitor_analyzer", "version": "1.0"}})
        self._rpc("notifications/initialized", {}, notify=True)
        self._connected = True
        return self

    def call_tool(self, name, arguments):
        self.connect()
        msgs = self._rpc("tools/call", {"name": name, "arguments": arguments})
        for m in msgs:
            for c in (m.get("result") or {}).get("content") or []:
                text = c.get("text") if isinstance(c, dict) else str(c)
                if not text:
                    continue
                try:
                    return json.loads(text)
                except json.JSONDecodeError:
                    raise MCPError(f"{name} 返回非 JSON")
        raise MCPError(f"{name} 无返回")

    def tools(self):
        if self._tools is None:
            names = []
            for m in self._rpc("tools/list", {}):
                for t in (m.get("result") or {}).get("tools") or []:
                    names.append(t.get("name"))
            self._tools = names
        return self._tools

    def __enter__(self):
        return self.connect()

    def __exit__(self, *exc):
        return False


def fetch_asin_bsr(client, asin, country="US"):
    data = client.call_tool("get_asin_bsr", {
        "asin": asin, "country": country,
        "intent_summary": "校验候选竞品的类目 BSR，用于筛掉非头部产品。",
        "user_task": f"查询 ASIN {asin} 在 {country} 站的 BSR。",
    }).get("data") or {}
    tree = data.get("amzCategoryTree") or {}
    ranks = data.get("classificationRanks") or []
    path = tree.get("path") or []
    leaf_id = tree.get("categoryId")
    leaf_name = None
    for p in path:
        if p.get("categoryId") == leaf_id:
            leaf_name = p.get("name") or p.get("displayName")
    by_id = {r.get("classificationId"): r for r in ranks}
    primary = by_id.get(leaf_id)
    if primary is None and ranks:
        primary = min(ranks, key=lambda r: r.get("rank") or 10 ** 9)
    return {
        "bsr_rank": (primary or {}).get("rank"),
        "bsr_category": (primary or {}).get("displayName") or leaf_name,
        "leaf_id": leaf_id,
        "path": [p.get("name") or p.get("displayName") for p in path],
        "ranks": [{"category": r.get("displayName"), "rank": r.get("rank")} for r in ranks],
    }


def fetch_asin_sales(client, asin, country="US"):
    """尝试用西柚销量类工具补近 30 天销量；未开放则返回 None。"""
    for name in client.tools():
        low = name.lower()
        if "sales" in low or "销量" in name:
            try:
                data = client.call_tool(name, {
                    "asin": asin, "country": country,
                    "intent_summary": "补充竞品近 30 天销量，用于竞争强度判断。",
                    "user_task": f"查询 ASIN {asin} 在 {country} 站的销量数据。",
                }).get("data") or {}
            except Exception:
                continue
            for key in ("sales30d", "monthSales", "salesLast30Days", "sales", "monthlySales"):
                if data.get(key) not in (None, ""):
                    return data[key]
            node = data.get("asinSales") or data.get("salesInfo") or {}
            if isinstance(node, dict):
                for key in ("sales30d", "monthSales", "sales", "monthlySales"):
                    if node.get(key) not in (None, ""):
                        return node[key]
    return None
