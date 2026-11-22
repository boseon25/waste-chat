
from pathlib import Path

import geopandas as gpd
import matplotlib.pyplot as plt


# ============================================================
# 1. 데이터 경로
# ============================================================

SHAPEFILE_PATH = Path(
    "/home/a202192012/5.Agent_System/프로젝트/"
    "산업자원_순환·재활용_에이전트(MS)/src/"
    "행정구역데이터셋_지도/"
    "1. 2025년 2분기 기준 시도 경계/"
    "bnd_sido_00_2025_2Q.shp"
)


def load_shapefile(path: Path) -> gpd.GeoDataFrame:
    """Shapefile을 읽고 유효성을 검사합니다."""

    if not path.exists():
        raise FileNotFoundError(f"파일을 찾을 수 없습니다: {path}")

    required_files = [".shp", ".shx", ".dbf"]

    for ext in required_files:
        component = path.with_suffix(ext)
        if not component.exists():
            raise FileNotFoundError(
                f"필수 Shapefile 구성 파일이 없습니다: {component}"
            )

    gdf = gpd.read_file(path)

    if gdf.empty:
        raise ValueError("Shapefile에 데이터가 없습니다.")

    return gdf


def print_information(gdf: gpd.GeoDataFrame) -> None:
    """공간 데이터의 기본 정보를 출력합니다."""

    print("\n" + "=" * 60)
    print("1. 데이터 기본 정보")
    print("=" * 60)

    print(f"전체 시도 개수: {len(gdf)}")
    print(f"좌표계(CRS): {gdf.crs}")
    print(f"Geometry 타입: {gdf.geom_type.unique()}")
    print(f"좌표 범위: {gdf.total_bounds}")

    print("\n" + "=" * 60)
    print("2. 컬럼 정보")
    print("=" * 60)

    print(gdf.columns.tolist())

    print("\n" + "=" * 60)
    print("3. 행정구역 속성 데이터")
    print("=" * 60)

    print(gdf.drop(columns="geometry").to_string(index=False))

    print("\n" + "=" * 60)
    print("4. Geometry 유효성")
    print("=" * 60)

    print(f"유효한 Geometry: {gdf.geometry.is_valid.sum()}")
    print(f"유효하지 않은 Geometry: {(~gdf.geometry.is_valid).sum()}")


def visualize_map(gdf: gpd.GeoDataFrame) -> None:
    """전국 시도 경계 지도를 시각화합니다."""

    fig, ax = plt.subplots(figsize=(12, 14))

    gdf.plot(
        ax=ax,
        edgecolor="black",
        linewidth=0.7,
        color="lightblue",
        alpha=0.8,
    )

    # 행정구역 이름 후보 컬럼 탐색
    name_candidates = [
        "SIDO_NM",
        "CTP_KOR_NM",
        "sido_nm",
        "NAME",
        "name",
    ]

    name_column = next(
        (col for col in name_candidates if col in gdf.columns),
        None,
    )

    # 시도 이름 표시
    if name_column:
        for _, row in gdf.iterrows():
            point = row.geometry.representative_point()

            ax.annotate(
                text=str(row[name_column]),
                xy=(point.x, point.y),
                ha="center",
                fontsize=8,
            )

    ax.set_title(
        "South Korea Administrative Division - SIDO (2025 Q2)",
        fontsize=14,
        pad=15,
    )

    ax.set_axis_off()
    plt.tight_layout()
    plt.show()


def main() -> None:
    try:
        gdf = load_shapefile(SHAPEFILE_PATH)

        print_information(gdf)
        visualize_map(gdf)

    except FileNotFoundError as e:
        print(f"[파일 오류] {e}")

    except Exception as e:
        print(f"[실행 오류] {type(e).__name__}: {e}")


if __name__ == "__main__":
    main()
