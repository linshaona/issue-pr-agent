import re
import requests




def parse_issue_target(target_str) -> tuple[str, str, int]:
    """
    将url拆分成owner,repo,issue_num
    :param target_str:
    :return: owner,repo,issue_num
    """
    url_pattern = r"github\.com/(?P<owner>[^/]+)/(?P<repo>[^/]+)/issues/(?P<issue_number>\d+)"
    short_pattern = r"^(?P<owner>[^/#]+)/(?P<repo>[^/#]+)#(?P<issue_number>\d+)$"
    match = re.search(url_pattern,target_str)

    if not match:
        match = re.search(short_pattern, target_str)

    if match:
        owner : str = match.group('owner')
        repo : str= match.group('repo')
        issue_num : int= int(match.group('issue_number'))
        return owner,repo,issue_num

    return "","",-1


def fetch_github_issue(owner, repo, issue_number, token=None) -> dict:
    """
    抓取issue
    :param owner:
    :param repo:
    :param issue_number:
    :param token:
    :return:
    """

    api_url = f"https://api.github.com/repos/{owner}/{repo}/issues/{issue_number}"

    # 1. 组装请求头
    headers = {
        "Accept": "application/vnd.github+json",  # GitHub 官方推荐带上的 API 版本头
        "User-Agent": "issue-pr-agent"  # GitHub 要求所有 API 请求最好有 User-Agent
    }

    # 2. 如果配置了 token，就追加 Authorization
    if token:
        headers["Authorization"] = f"Bearer {token}"

    # 3 发送get请求
    resp = requests.get(api_url,headers=headers)

    # 4. 校验状态码
    if resp.status_code == 200:
        return resp.json()
    elif resp.status_code == 404:
        raise Exception(f"Issue 未找到: {owner}/{repo}#{issue_number}")
    elif resp.status_code == 403:
        raise Exception("requests reject (403): 可能是 API 限流了，或者 Token 权限不足。")
    else:
        raise Exception(f"GitHub API requests fail，status code: {resp.status_code}")

def format_issue(issue_data) -> str:
    """
    格式化issue
    :param issue_data:
    :return:
    """
    title = issue_data.get("title","unknow title")
    state = issue_data.get("state","unknow state")
    author = issue_data.get("user", {}).get("login", "unknow author")

    label_names = [label["name"] for label in issue_data.get("labels", [])]
    labels_str = ", ".join(label_names) if label_names else "no"

    body = issue_data.get("body") or "(no description)"

    output = f"""
    ============================================================
    【Issue 详情】
    - 标题: {title}
    - 状态: {state.upper()}
    - 作者: @{author}
    - 标签: [{labels_str}]
    ------------------------------------------------------------
    【正文描述】:
    {body.strip()}
    ============================================================
    """
    return output

def handle_url(target:str,github_token : str = "")->str:
    parse_result = parse_issue_target(target)

    owner, repo, issue_number = parse_result
    #print(f"切分结果 owner:{owner}\nrepo:{repo}\nissue:{issue_number}\n")

    if issue_number == -1:
        print("解析错误！")
        return ""

    fetch_result = fetch_github_issue(owner, repo, issue_number, github_token)

    #print(f"fetch result:\n{fetch_result}\n")

    format_result = format_issue(fetch_result)

    return format_result or ""
    # print(format_result)

