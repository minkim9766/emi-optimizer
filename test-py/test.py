from tester import fill_gerber_outline_to_region

info = fill_gerber_outline_to_region(
    "../Test Files/Test1/_33-B_Adhes.gbr",
    "tmp/test_out.gbr",
    snap_tol_mm=0.05,      # 필요 시 조정
    max_seg_len_mm=0.1,    # 원호 근사 정밀도 강화
    max_angle_deg=3.0
)
print(info)
