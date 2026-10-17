# Icinga Image Builder

Tool độc lập để build và push image cho Icinga, theo cách vận hành gần với `kolla-build`:

- version của từng thành phần nằm trong một file YAML;
- chọn build toàn bộ hoặc một/nhiều image theo cú pháp như `kolla-build`;
- pull base image từ Docker Hub hoặc registry/mirror do người vận hành chỉ định;
- tag local hoặc push lên Harbor/registry riêng;
- Dockerfile mặc định được đóng gói cùng tool; custom bằng Dockerfile override bên ngoài virtualenv như Kolla;
- tự chuyển proxy đang có trong environment của build node vào `docker build`;
- xuất YAML `custom_image_registry` và `custom_image_overrides` để đưa vào `icinga-ansible`.

Project này không import code, không đọc mặc định `/etc/icinga-ansible`, và không cần cài chung với
`icinga-ansible`. Hai project chỉ giao tiếp qua tên/tag image trong registry.

## 1. Cài trong virtual environment

Trên build node đã có Docker:

```bash
sudo apt install -y python3-venv
python3 -m venv /path/to/venv
source /path/to/venv/bin/activate
python -m pip install -U pip
python -m pip install /path/to/icinga-image-builder
icinga-image-build --help
```

Dockerfile mặc định được đóng gói vào virtualenv, tương tự Kolla cài template mặc định vào
`$VIRTUAL_ENV/share/kolla/docker`. Không sửa bản mặc định trong source tree sau khi cài. Khi cần custom,
đặt Dockerfile override bên ngoài virtualenv và khai bằng `docker_dirs` hoặc `--docker-dir`. Sửa file
override rồi build lại sẽ có hiệu lực ngay; không cần cài lại tool và không cần `pip install -e`.

Docker chạy bằng user hiện tại. Nếu Docker socket chỉ cho root, thêm user build vào group `docker`,
đăng nhập lại và kiểm tra `docker info`. Không chạy cả virtual environment bằng `sudo`.

## 2. Cấu hình version và registry

Sửa `config.yml`:

```yaml
versions:
  icinga2: "2.16.5"
  icingadb: "1.5.1"
  icingadb_redis: "7.4.11"
  icingaweb2: "2.14.0"
  mariadb: "12.3.3"

source_registry: ""
target_registry: "172.28.81.109/openstack.kolla/icinga"
local_namespace: "icinga-local"
tag_suffix: ""                    # mặc định tag đúng version đã pin
platform: "linux/amd64"
build_network: ""                  # đặt "host" nếu build node không có Docker bridge
retries: 1                         # retry thêm một lần khi Docker/registry lỗi tạm thời

docker_dirs:                       # optional; thư mục override ngoài virtualenv
  - "/etc/icinga-image-builder/dockerfiles"
```

`source_registry` và `target_registry` chỉ chứa `host[:port][/project]`, không có `https://`.

- `source_registry: ""`: base image lấy từ Docker Hub.
- `source_registry: "harbor.example.com/dockerhub"`: base image Icinga 2 trở thành
  `harbor.example.com/dockerhub/icinga/icinga2:2.16.5`. Project Harbor này phải là proxy cache
  hoặc đã chứa đúng repository nguồn.
- `target_registry`: nơi nhận image custom khi push. Project phải tồn tại và tài khoản đã có quyền.
- Nếu để trống `target_registry`, build vẫn chạy và tag dưới `local_namespace`; command `push` sẽ
  từ chối để tránh đẩy nhầm nơi.

### `local_namespace` dùng khi nào?

`local_namespace` là tiền tố đặt tên image chỉ trên Docker daemon của build node khi
`target_registry` để trống. Nó không phải registry, Harbor project, Docker network hay một tài nguyên
cần tạo trước. Docker chỉ lưu toàn bộ chuỗi này như tên repository của image local.

Ví dụ:

```yaml
target_registry: ""
local_namespace: "icinga-local"
```

Với version `icinga2: 2.16.5`, target được tạo là:

```text
icinga-local/icinga2:2.16.5
```

Nếu có `tag_suffix: "-custom"`, target trở thành:

```text
icinga-local/icinga2:2.16.5-custom
```

Khi đã khai registry đích, tool ưu tiên hoàn toàn `target_registry`:

```yaml
target_registry: "172.28.81.109/openstack.kolla/icinga"
local_namespace: "icinga-local"
```

Target khi đó là:

```text
172.28.81.109/openstack.kolla/icinga/icinga2:2.16.5
```

`local_namespace` không xuất hiện trong tên trên và không ảnh hưởng tới image production. Giá trị này
được giữ để build/test local có namespace riêng, tránh tạo tag ngắn như `icinga2:2.16.5` dễ nhầm với
image upstream. Lệnh `push` vẫn yêu cầu `target_registry`; tool không suy đoán một registry từ
`local_namespace`.

Quy tắc chọn target:

```text
target_registry có giá trị  -> target_registry/image:version[-suffix]
target_registry để trống    -> local_namespace/image:version[-suffix]
```

Kiểm tra trước mọi thay đổi:

```bash
icinga-image-build plan --config ./config.yml
```

Có thể ghi đè registry tại command line mà không sửa file:

```bash
icinga-image-build plan --config ./config.yml \
  --source-registry harbor.example.com/dockerhub \
  --registry harbor.example.com/icinga
```

## 3. Build

Build đủ năm image đúng version đã pin:

```bash
icinga-image-build build --config ./config.yml --pull
```

Chỉ build Icinga 2, theo cú pháp positional giống Kolla build:

```bash
icinga-image-build build icinga2 --config ./config.yml --pull
```

Nếu Docker báo `network bridge not found`, dùng network host của build node:

```bash
icinga-image-build build icinga2 --config ./config.yml --pull --network host
```

Chọn nhiều image bằng cách ghi tên liên tiếp:

```bash
icinga-image-build build icinga2 icingadb icingaweb2 --config ./config.yml
```

Option cũ vẫn được hỗ trợ để tương thích:

```bash
icinga-image-build build --config ./config.yml --image icinga2,icingadb
```

Build không cache và push ngay:

```bash
docker login 172.28.81.109
icinga-image-build build --config ./config.yml --pull --no-cache --push
```

Tool không nhận password registry trên command line. Nó dùng credential store của Docker sau
`docker login`, tránh để mật khẩu trong shell history hoặc process list.

Push các image đã build từ trước:

```bash
icinga-image-build push --config ./config.yml
```

## 4. Dockerfile mặc định và custom theo cách Kolla

Dockerfile mặc định của cả năm image được đóng gói cùng Python package. Nếu không khai `docker_dirs`
và không khai `images.<name>.dockerfile`, tool dùng bản mặc định trong virtualenv. Các file này chỉ kế
thừa image upstream theo version đã pin nên không tự cài thêm package.

Custom image nên nằm ngoài virtualenv với cấu trúc giống `--docker-dir` của Kolla:

```text
/etc/icinga-image-builder/dockerfiles/
├── icinga2/
│   └── Dockerfile
└── icingaweb2/
    └── Dockerfile
```

Chỉ cần tạo thư mục cho image muốn custom. Các image không xuất hiện trong thư mục override tiếp tục dùng
Dockerfile mặc định. Khai trong `config.yml`:

```yaml
docker_dirs:
  - "/etc/icinga-image-builder/dockerfiles"
```

Cũng có thể truyền từ command line, tương tự Kolla:

```bash
icinga-image-build build icinga2 --config ./config.yml \
  --docker-dir /etc/icinga-image-builder/dockerfiles
```

Có thể khai `docker_dirs` hoặc `--docker-dir` nhiều lần. Tool duyệt theo thứ tự và Dockerfile trong thư
mục sau sẽ ghi đè Dockerfile cùng image từ thư mục trước.

`versions` trong `config.yml` là nguồn duy nhất của version base image. Dockerfile mặc định không chứa
version cố định; tool ghép `source_registry + source_repository + version` rồi bắt buộc truyền kết quả
qua build argument `BASE_IMAGE`. Không được khai `BASE_IMAGE` trong `images.<name>.build_args`.

Khi cần custom Icinga 2, copy file mẫu thành
`/etc/icinga-image-builder/dockerfiles/icinga2/Dockerfile`. Tool luôn truyền `BASE_IMAGE` theo version đã
chọn. Project có file mẫu `examples/icinga2-openstack/Dockerfile` cài Python và OpenStack SDK:

```dockerfile
# check=skip=InvalidDefaultArgInFrom

ARG BASE_IMAGE
FROM ${BASE_IMAGE}

ARG OPENSTACKSDK_VERSION=4.21.0
ARG PYYAML_VERSION=6.0.3

USER root
RUN apt-get update \
    && apt-get install -y --no-install-recommends python3 python3-venv \
    && python3 -m venv /opt/icinga-venv \
    && /opt/icinga-venv/bin/pip install --no-cache-dir \
       "openstacksdk==${OPENSTACKSDK_VERSION}" "PyYAML==${PYYAML_VERSION}" \
    && rm -rf /var/lib/apt/lists/*

USER icinga
```

Nếu một image cần build context đặc biệt, vẫn có thể trỏ trực tiếp bằng `dockerfile` và `context`. Đường
dẫn tương đối được tính từ thư mục chứa `config.yml`; cấu hình riêng theo image có ưu tiên cao hơn
`docker_dirs`:

```yaml
images:
  icinga2:
    dockerfile: "examples/icinga2-openstack/Dockerfile"
    context: "examples/icinga2-openstack"
    build_args:
      OPENSTACKSDK_VERSION: "4.21.0"
      PYYAML_VERSION: "6.0.3"
```

Theo cách dùng mặc định của Kolla, image custom giữ nguyên tag version đã pin. Ví dụ trên vì vậy tạo
`icinga2:2.16.5`; `icinga-ansible` không cần đổi `custom_image_overrides` chỉ vì image có thêm Python hoặc
OpenStack SDK. Kolla cũng có `openstack_tag_suffix`, nhưng mặc định để rỗng và tài liệu dùng suffix cho biến
thể cần tồn tại song song, chẳng hạn image `-aarch64`. Tool vẫn hỗ trợ `tag_suffix` cho đúng trường hợp đó,
nhưng không đặt suffix cho image custom thông thường.

Có thể chạy ngay ví dụ đi kèm:

```bash
icinga-image-build build icinga2 \
  --config ./examples/icinga2-openstack/config.yml
```

Tool truyền `http_proxy`, `https_proxy`, `no_proxy` cùng các biến viết hoa từ environment hiện tại.
Nếu build node dùng `/etc/environment`, đăng nhập lại hoặc nạp các biến đó vào shell trước khi build.

### Xác minh thay đổi trong `docker_dirs` được dùng ngay

Quy trình dưới đây đã được kiểm chứng bằng build thật trên Docker 29.8.1. Chạy trên build node có Docker;
không cần chạy lại `pip install` giữa hai lần build.

Đặt suffix riêng để image kiểm tra không ghi đè image đang sử dụng:

```yaml
tag_suffix: "-docker-dir-test"
docker_dirs:
  - "/etc/icinga-image-builder/dockerfiles"
```

Tạo `/etc/icinga-image-builder/dockerfiles/icinga2/Dockerfile`:

```dockerfile
# check=skip=InvalidDefaultArgInFrom

ARG BASE_IMAGE
FROM ${BASE_IMAGE}

USER root
RUN printf 'override-v1\n' > /docker-dir-test-version
```

Build và đọc file marker trong image:

```bash
icinga-image-build build icinga2 --config ./config.yml --no-cache

docker run --rm --entrypoint cat \
  icinga-local/icinga2:2.16.5-docker-dir-test \
  /docker-dir-test-version
```

Ví dụ này giả sử `target_registry` để trống và `local_namespace: "icinga-local"`. Nếu build vào registry,
dùng đúng tên target mà lệnh `icinga-image-build` in ra.

Kết quả lần đầu phải là:

```text
override-v1
```

Chỉ sửa dòng `RUN` trong chính Dockerfile override:

```dockerfile
RUN printf 'override-v2\n' > /docker-dir-test-version
```

Chạy lại đúng hai lệnh `icinga-image-build build` và `docker run` ở trên, không cài lại tool. Kết quả
phải đổi thành:

```text
override-v2
```

`--no-cache` được dùng trong bài kiểm tra để loại trừ Docker layer cache. Trong vận hành bình thường có
thể bỏ option này; Docker vẫn phát hiện Dockerfile đã thay đổi và rebuild layer liên quan.

## 5. Dùng image với icinga-ansible

Xuất file riêng khi lập kế hoạch hoặc build:

```bash
icinga-image-build plan --config ./config.yml \
  --output-overrides ./icinga-ansible-images.yml
```

Nội dung sinh ra có dạng:

```yaml
custom_image_registry: 172.28.81.109/openstack.kolla/icinga
custom_image_overrides:
  icinga2: icinga2:2.16.5
  icingadb: icingadb:1.5.1
  icingadb_redis: icingadb-redis:7.4.11
  icingaweb2: icingaweb2:2.14.0
  mariadb: mariadb:12.3.3
```

Merge hai key này vào `/etc/icinga-ansible/globals.yml`, sau đó chạy `icinga-ansible pull`,
`prechecks` và `deploy`/`upgrade` theo runbook của project đó.

## 6. Kiểm tra image đã push

```bash
docker manifest inspect 172.28.81.109/openstack.kolla/icinga/icinga2:2.16.5 >/dev/null
docker pull 172.28.81.109/openstack.kolla/icinga/icinga2:2.16.5
docker image inspect 172.28.81.109/openstack.kolla/icinga/icinga2:2.16.5 \
  --format '{{ index .Config.Labels "org.opencontainers.image.base.name" }}'
```

Với image Icinga 2 custom dùng tag version đã pin, kiểm tra package:

```bash
docker run --rm --entrypoint /opt/icinga-venv/bin/python \
  172.28.81.109/openstack.kolla/icinga/icinga2:2.16.5 \
  -c 'import openstack, yaml; print(openstack.__version__)'
```

## 7. Quy tắc an toàn

- Mọi version phải được khai rõ theo dạng `x.y.z`; tool không tự dùng `latest`.
- `plan` không gọi Docker và không thay đổi máy.
- `build` không tự push nếu thiếu `--push`.
- `push` bị chặn khi chưa có target registry.
- Build lỗi ở image nào thì dừng ngay ở image đó.
- Chỉ các image được chọn theo positional hoặc `--image` mới xuất hiện trong file override.
