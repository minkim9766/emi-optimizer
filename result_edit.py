# input.txt 파일이 현재 스크립트와 같은 경로에 있어야 합니다.
# 파일이 없으면 이 코드를 실행하기 전에 임의의 텍스트로 파일을 생성해주세요.

input_file_name = r'C:\Users\unid9\Desktop\EMI-Optimizer\emi-optimizer\result.txt'
output_file_name = r'C:\Users\unid9\Desktop\EMI-Optimizer\emi-optimizer\result1.txt'

try:
    # 1. 원본 파일을 읽기 모드(r)로 엽니다.
    with open(input_file_name, 'r', encoding='utf-8') as infile:
        # 2. 저장할 파일을 쓰기 모드(w)로 엽니다. 파일이 없으면 새로 생성됩니다.
        with open(output_file_name, 'w', encoding='utf-8') as outfile:
            # 원본 파일의 내용을 한 줄씩 읽어옵니다.
            for line in infile:
                # 각 줄의 문자를 개별적으로 반복합니다.
                for char in line:
                    # 원본 파일의 개행 문자는 무시하고 출력합니다.
                    if char != '\n':
                        # 각 문자를 쓰고, write()는 자동 줄바꿈이 없으므로 \n을 추가합니다.
                        outfile.write(char + '\n')
    
    print(f"'{input_file_name}'의 각 문자가 '{output_file_name}' 파일에 성공적으로 저장되었습니다.")

except FileNotFoundError:
    print(f"Error: '{input_file_name}' 파일을 찾을 수 없습니다. 파일 경로 및 이름을 확인해주세요.")
except Exception as e:
    print(f"파일 처리 중 오류 발생: {e}")

