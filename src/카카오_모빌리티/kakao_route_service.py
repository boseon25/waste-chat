
from __future__ import annotations

import os
from typing import Any

import httpx
from dotenv import load_dotenv
from pydantic import BaseModel, Field

load_dotenv()

BASE_URL = "https://apis-navi.kakaomobility.com/v1/directions"


class Coordinate(BaseModel):
    longitude: float = Field(ge=-180, le=180)
    latitude: float = Field(ge=-90, le=90)


class RouteResult(BaseModel):
    distance_km: float
    duration_min: float
    toll_fee: int
    path: list[tuple[float, float]] = Field(default_factory=list)


class KakaoRouteService:
    def __init__(self) -> None:
        api_key = os.getenv("KAKAO_REST_API_KEY")

        if not api_key:
            raise ValueError("KAKAO_REST_API_KEY가 설정되지 않았습니다.")

        self.headers = {
            "Authorization": f"KakaoAK {api_key}",
            "Content-Type": "application/json",
        }

    async def get_route(
        self,
        origin: Coordinate,
        destination: Coordinate,
        priority: str = "RECOMMEND",
        alternatives: bool = True,
    ) -> list[RouteResult]:

        if priority not in {"RECOMMEND", "TIME", "DISTANCE"}:
            raise ValueError("지원하지 않는 경로 우선순위입니다.")

        params = {
            "origin": f"{origin.longitude},{origin.latitude}",
            "destination": (
                f"{destination.longitude},{destination.latitude}"
            ),
            "priority": priority,
            "alternatives": str(alternatives).lower(),
            "summary": "false",
            "road_details": "true",
        }

        async with httpx.AsyncClient(timeout=20.0) as client:
            response = await client.get(
                BASE_URL,
                headers=self.headers,
                params=params,
            )

            response.raise_for_status()
            data: dict[str, Any] = response.json()

        routes = data.get("routes", [])
        results: list[RouteResult] = []

        for route in routes:
            if route.get("result_code") != 0:
                continue

            summary = route["summary"]

            # 도로 경로 좌표 추출
            path: list[tuple[float, float]] = []

            for section in route.get("sections", []):
                for road in section.get("roads", []):
                    vertices = road.get("vertexes", [])

                    for i in range(0, len(vertices) - 1, 2):
                        path.append(
                            (vertices[i], vertices[i + 1])
                        )

            results.append(
                RouteResult(
                    distance_km=summary["distance"] / 1000,
                    duration_min=summary["duration"] / 60,
                    toll_fee=summary["fare"]["toll"],
                    path=path,
                )
            )

        if not results:
            raise RuntimeError("사용 가능한 경로를 찾지 못했습니다.")

        return results
