
from pathlib import Path

import geopandas as gpd
import pandas as pd


# ============================================================
# 설정
# ============================================================

BASE_DIR = Path(
    "/home/a202192012/5.Agent_System/프로젝트/"
    "산업자원_순환·재활용_에이전트(MS)/src/"
    "행정구역데이터셋_지도"
)

SHAPEFILE_PATH = (
    BASE_DIR
    / "2. 2025년 2분기 기준 시군구 경계"
    / "bnd_sigungu_00_2025_2Q.shp"
)

OUTPUT_PATH = BASE_DIR / "sigungu_centers_2025_2Q.csv"


def load_boundary(path: Path) -> gpd.GeoDataFrame:
    """시군구 경계 Shapefile 로드."""

    if not path.exists():
        raise FileNotFoundError(f"Shapefile이 없습니다: {path}")

    gdf = gpd.read_file(path)

    if gdf.empty:
        raise ValueError("시군구 경계 데이터가 비어 있습니다.")

    if gdf.crs is None:
        raise ValueError("좌표계(CRS)가 정의되지 않았습니다.")

    return gdf



def generate_centers(gdf: gpd.GeoDataFrame) -> pd.DataFrame:
    """시군구 경계의 대표점 및 중심점 좌표를 생성합니다."""

    # 실제 Shapefile 컬럼명
    code_col = "SIGUNGU_CD"
    name_col = "SIGUNGU_NM"

    required_columns = [code_col, name_col, "geometry"]
    missing_columns = [
        col for col in required_columns
        if col not in gdf.columns
    ]

    if missing_columns:
        raise ValueError(
            f"필요한 컬럼이 없습니다: {missing_columns}\n"
            f"현재 컬럼: {list(gdf.columns)}"
        )

    if gdf.crs is None:
        raise ValueError("좌표계(CRS)가 정의되지 않았습니다.")

    if not gdf.crs.is_projected:
        raise ValueError(
            f"투영 좌표계가 필요합니다. 현재 좌표계: {gdf.crs}"
        )

    # 지오메트리 유효성 검사
    if gdf.geometry.isna().any():
        raise ValueError("geometry에 결측값이 존재합니다.")

    if gdf.geometry.is_empty.any():
        raise ValueError("geometry에 빈 도형이 존재합니다.")

    # 시군구 내부 대표점 계산
    representative_points = gdf.geometry.representative_point()

    # 기하학적 중심점 계산
    centroids = gdf.geometry.centroid

    # 좌표계 변환: EPSG:5179 -> EPSG:4326
    representative_gdf = gpd.GeoDataFrame(
        gdf[[code_col, name_col]].copy(),
        geometry=representative_points,
        crs=gdf.crs,
    ).to_crs(epsg=4326)

    centroid_gdf = gpd.GeoDataFrame(
        geometry=centroids,
        crs=gdf.crs,
    ).to_crs(epsg=4326)

    result = pd.DataFrame({
        "sig_cd": (
            gdf[code_col]
            .astype(str)
            .str.replace(r"\.0$", "", regex=True)
            .str.zfill(5)
        ),
        "sig_nm": gdf[name_col].astype(str),
        "longitude": representative_gdf.geometry.x,
        "latitude": representative_gdf.geometry.y,
        "centroid_longitude": centroid_gdf.geometry.x,
        "centroid_latitude": centroid_gdf.geometry.y,
        "coordinate_source": "administrative_representative_point",
    })

    return result



def main() -> None:
    try:
        print("=" * 60)
        print("시군구 중심 좌표 생성")
        print("=" * 60)

        # 1. 데이터 로드
        print("\n[1] 시군구 경계 데이터 로드")

        gdf = load_boundary(SHAPEFILE_PATH)

        print(f"시군구 개수: {len(gdf)}")
        print(f"좌표계: {gdf.crs}")
        print(f"컬럼: {gdf.columns.tolist()}")

        # 2. 중심 좌표 생성
        print("\n[2] 시군구 중심 좌표 계산")

        result = generate_centers(gdf)

        # 3. 결측 및 중복 검사
        if result[["longitude", "latitude"]].isna().any().any():
            raise ValueError("중심 좌표에 결측값이 존재합니다.")

        if result["sig_cd"].duplicated().any():
            raise ValueError("중복된 시군구 코드가 존재합니다.")

        # 4. CSV 저장
        print("\n[3] CSV 저장")

        result.to_csv(
            OUTPUT_PATH,
            index=False,
            encoding="utf-8-sig",
        )

        # 5. 결과 출력
        print(f"\n저장 위치: {OUTPUT_PATH}")
        print(f"생성 좌표 개수: {len(result)}")

        print("\n[4] 결과 미리보기")

        print(
            result[
                ["sig_cd", "sig_nm", "longitude", "latitude"]
            ].to_string(index=False)
        )

        print("\n[완료] 시군구 좌표 생성 성공")

    except Exception as e:
        print(f"\n[오류] {type(e).__name__}: {e}")
        raise


if __name__ == "__main__":
    main()
