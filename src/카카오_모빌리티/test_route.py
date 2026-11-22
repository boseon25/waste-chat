
import asyncio

from kakao_route_service import (
    KakaoRouteService,
    Coordinate,
)


async def main():
    service = KakaoRouteService()

    # 예시 좌표: 전주 지역이 아닌 판교 주변 테스트 좌표
    origin = Coordinate(
        longitude=127.1076419,
        latitude=37.4024648,
    )

    destination = Coordinate(
        longitude=127.1105633,
        latitude=37.3941969,
    )

    routes = await service.get_route(
        origin,
        destination,
        priority="RECOMMEND",
        alternatives=True,
    )

    for index, route in enumerate(routes, start=1):
        print(f"\n경로 {index}")
        print(f"거리: {route.distance_km:.2f} km")
        print(f"시간: {route.duration_min:.1f} 분")
        print(f"통행료: {route.toll_fee:,} 원")
        print(f"좌표 수: {len(route.path)}")


if __name__ == "__main__":
    asyncio.run(main())
