

"""슬레이브 노드가 마스터에 등록하고, 준비 완료를 알리고, 더미 청크를 전송한 뒤 완료를 보고하는 가장 기본적인 클라이언트 모듈."""

import asyncio

import httpx

from common.protocol import ChunkRequest, CompleteRequest, ReadyRequest, RegisterRequest


class SlaveNode:
    """ESP 슬레이브 노드 1대를 단순하게 흉내 내는 클래스."""

    def __init__(
        self,
        node_id: str,
        master_url: str,
        total_chunks: int = 5,
        delay_sec: float = 0.3,
    ) -> None:
        """슬레이브 노드의 기본 설정값을 저장한다."""

        self.node_id = node_id
        self.master_url = master_url
        self.total_chunks = total_chunks
        self.delay_sec = delay_sec

    async def register(self, client: httpx.AsyncClient) -> None:
        """마스터에 자신을 등록한다."""

        request = RegisterRequest(
            node_id=self.node_id,
            total_chunks=self.total_chunks,
        )
        response = await client.post(f"{self.master_url}/register", json=request.model_dump())
        print(f"[SLAVE][{self.node_id}] register 응답: {response.json()}")

    async def ready(self, client: httpx.AsyncClient) -> None:
        """마스터에 전송 준비 완료를 알린다."""

        request = ReadyRequest(node_id=self.node_id)
        response = await client.post(f"{self.master_url}/ready", json=request.model_dump())
        print(f"[SLAVE][{self.node_id}] ready 응답: {response.json()}")

    async def send_chunks(self, client: httpx.AsyncClient) -> None:
        """더미 문자열을 청크 단위로 생성해서 순서대로 전송한다."""

        for chunk_index in range(self.total_chunks):
            payload = f"{self.node_id}의 더미 데이터 청크 {chunk_index}"

            request = ChunkRequest(
                node_id=self.node_id,
                chunk_index=chunk_index,
                total_chunks=self.total_chunks,
                payload=payload,
            )
            response = await client.post(f"{self.master_url}/chunk", json=request.model_dump())
            print(f"[SLAVE][{self.node_id}] chunk {chunk_index} 전송 응답: {response.json()}")

            # 각 청크가 너무 빠르게 지나가지 않도록 잠깐 대기한다.
            await asyncio.sleep(self.delay_sec)

    async def complete(self, client: httpx.AsyncClient) -> None:
        """모든 청크 전송이 끝났음을 마스터에 알린다."""

        request = CompleteRequest(node_id=self.node_id)
        response = await client.post(f"{self.master_url}/complete", json=request.model_dump())
        print(f"[SLAVE][{self.node_id}] complete 응답: {response.json()}")

    async def run(self) -> None:
        """슬레이브 노드의 전체 동작 순서를 실행한다."""

        async with httpx.AsyncClient(timeout=10.0) as client:
            await self.register(client)
            await self.ready(client)
            await self.send_chunks(client)
            await self.complete(client)