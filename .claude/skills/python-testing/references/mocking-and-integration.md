# Python Testing Patterns: Mocking and Integration

## Contents

- Mocking and Patching
- Testing Async Code
- Testing Exceptions
- Testing Side Effects
- Test Organization
- Common Patterns
- pytest Configuration

## Mocking and Patching

### Mocking Functions

```python
from unittest.mock import patch, Mock, MagicMock

@patch("mypackage.external_api_call")
def test_with_mock(api_call_mock):
    """Test with mocked external API."""
    api_call_mock.return_value = {"status": "success"}

    result = my_function()

    api_call_mock.assert_called_once()
    assert result["status"] == "success"
```

### Mocking Return Values

```python
@patch("mypackage.Database.connect")
def test_database_connection(connect_mock):
    """Test with mocked database connection."""
    connect_mock.return_value = MockConnection()

    db = Database()
    db.connect()

    connect_mock.assert_called_once_with("localhost")
```

### Mocking Exceptions

```python
@patch("mypackage.api_call")
def test_api_error_handling(api_call_mock):
    """Test error handling with mocked exception."""
    api_call_mock.side_effect = ConnectionError("Network error")

    with pytest.raises(ConnectionError):
        api_call()

    api_call_mock.assert_called_once()
```

### Mocking File I/O

```python
from unittest.mock import mock_open, patch

@patch("builtins.open", new_callable=mock_open, read_data="file content")
def test_file_reading(mock_file):
    """Test file reading with mocked open."""
    result = read_file("test.txt")

    mock_file.assert_called_once_with("test.txt", "r")
    assert result == "file content"
```

### Using autospec

```python
@patch("mypackage.DBConnection", autospec=True)
def test_autospec(db_mock):
    """Test with autospec to catch API misuse."""
    db = db_mock.return_value
    db.query("SELECT * FROM users")

    # Calls with wrong signatures fail at test time
    db_mock.assert_called_once()
```

### Mock Class Instances

```python
class TestUserService:
    @patch("mypackage.UserRepository")
    def test_create_user(self, repo_mock):
        """Test user creation with mocked repository."""
        repo_mock.return_value.save.return_value = User(id=1, name="Alice")

        service = UserService(repo_mock.return_value)
        user = service.create_user(name="Alice")

        assert user.name == "Alice"
        repo_mock.return_value.save.assert_called_once()
```

### Mock Properties

```python
from unittest.mock import Mock, PropertyMock

@pytest.fixture
def mock_config():
    """Create a mock with properties."""
    config = Mock()
    type(config).debug = PropertyMock(return_value=True)
    type(config).api_key = PropertyMock(return_value="test-key")
    return config

def test_with_mock_config(mock_config):
    """Test with mocked config properties."""
    assert mock_config.debug is True
    assert mock_config.api_key == "test-key"
```

## Testing Async Code

### Async Tests with pytest-asyncio

```python
import pytest

@pytest.mark.asyncio
async def test_async_function():
    """Test async function."""
    result = await async_add(2, 3)
    assert result == 5

@pytest.mark.asyncio
async def test_async_with_fixture(async_client):
    """Test async with async fixture."""
    response = await async_client.get("/api/users")
    assert response.status_code == 200
```

### Async Fixtures

```python
import pytest_asyncio

@pytest_asyncio.fixture
async def async_client():
    """Async fixture providing async test client."""
    app = create_app()
    async with app.test_client() as client:
        yield client

@pytest.mark.asyncio
async def test_api_endpoint(async_client):
    """Test using async fixture."""
    response = await async_client.get("/api/data")
    assert response.status_code == 200
```

### Mocking Async Functions

```python
from unittest.mock import AsyncMock, patch

@pytest.mark.asyncio
@patch("mypackage.async_api_call", new_callable=AsyncMock)
async def test_async_mock(api_call_mock):
    """Test async function with mock."""
    api_call_mock.return_value = {"status": "ok"}

    result = await my_async_function()

    api_call_mock.assert_awaited_once()
    assert result["status"] == "ok"
```

## Testing Exceptions

### Testing Expected Exceptions

```python
def test_divide_by_zero():
    """Test that dividing by zero raises ZeroDivisionError."""
    with pytest.raises(ZeroDivisionError):
        divide(10, 0)

def test_custom_exception():
    """Test custom exception with message."""
    with pytest.raises(ValueError, match="invalid input"):
        validate_input("invalid")
```

### Testing Exception Attributes

```python
def test_exception_with_details():
    """Test exception with custom attributes."""
    with pytest.raises(CustomError) as exc_info:
        raise CustomError("error", code=400)

    assert exc_info.value.code == 400
    assert "error" in str(exc_info.value)
```

## Testing Side Effects

### Testing with tmp_path Fixture

```python
def test_with_tmp_path(tmp_path):
    """Test using pytest's built-in tmp_path fixture (pathlib.Path)."""
    test_file = tmp_path / "test.txt"
    test_file.write_text("hello world")

    result = process_file(str(test_file))
    assert result == "hello world"
    # tmp_path is automatically cleaned up
```

### Capturing stdout/stderr

```python
def test_print_output(capsys):
    """Use capsys to assert against stdout/stderr."""
    print("hello")
    captured = capsys.readouterr()
    assert captured.out == "hello\n"
```

### Monkeypatching Environment

```python
def test_with_env_var(monkeypatch):
    """Use monkeypatch to safely set env vars or attributes."""
    monkeypatch.setenv("API_KEY", "test-key")
    assert os.environ["API_KEY"] == "test-key"
    # Reverted automatically after the test
```

## Test Organization

### Directory Structure

```text
tests/
├── conftest.py                 # Shared fixtures
├── __init__.py
├── unit/                       # Unit tests
│   ├── __init__.py
│   ├── test_models.py
│   ├── test_utils.py
│   └── test_services.py
├── integration/                # Integration tests
│   ├── __init__.py
│   ├── test_api.py
│   └── test_database.py
└── e2e/                        # End-to-end tests
    ├── __init__.py
    └── test_user_flow.py
```

### Test Classes

```python
class TestUserService:
    """Group related tests in a class."""

    @pytest.fixture(autouse=True)
    def setup(self):
        """Setup runs before each test in this class."""
        self.service = UserService()

    def test_create_user(self):
        """Test user creation."""
        user = self.service.create_user("Alice")
        assert user.name == "Alice"

    def test_delete_user(self):
        """Test user deletion."""
        user = User(id=1, name="Bob")
        self.service.delete_user(user)
        assert not self.service.user_exists(1)
```

## Common Patterns

### Testing Flask API Endpoints

```python
@pytest.fixture
def client():
    app = create_app(testing=True)
    return app.test_client()

def test_get_user(client):
    response = client.get("/api/users/1")
    assert response.status_code == 200
    assert response.json["id"] == 1

def test_create_user(client):
    response = client.post("/api/users", json={
        "name": "Alice",
        "email": "alice@example.com",
    })
    assert response.status_code == 201
    assert response.json["name"] == "Alice"
```

### Testing Database Operations

```python
@pytest.fixture
def db_session():
    """Create a transactional test database session (SQLAlchemy 2.x)."""
    connection = engine.connect()
    outer_transaction = connection.begin()
    try:
        with Session(
            bind=connection,
            join_transaction_mode="create_savepoint",
        ) as session:
            yield session
    finally:
        try:
            outer_transaction.rollback()
        finally:
            connection.close()

def test_create_user(db_session):
    user = User(name="Alice", email="alice@example.com")
    db_session.add(user)
    db_session.commit()

    retrieved = db_session.query(User).filter_by(name="Alice").first()
    assert retrieved.email == "alice@example.com"
```

### Testing Class Methods

```python
class TestCalculator:
    @pytest.fixture
    def calculator(self):
        return Calculator()

    def test_add(self, calculator):
        assert calculator.add(2, 3) == 5

    def test_divide_by_zero(self, calculator):
        with pytest.raises(ZeroDivisionError):
            calculator.divide(10, 0)
```

## pytest Configuration

### pyproject.toml (preferred)

```toml
[tool.pytest.ini_options]
testpaths = ["tests"]
python_files = ["test_*.py"]
python_classes = ["Test*"]
python_functions = ["test_*"]
addopts = [
    "--strict-markers",
    "--cov=mypackage",
    "--cov-report=term-missing",
    "--cov-report=html",
]
markers = [
    "slow: marks tests as slow",
    "integration: marks tests as integration tests",
    "unit: marks tests as unit tests",
]
```

### pytest.ini (legacy)

```ini
[pytest]
testpaths = tests
python_files = test_*.py
python_classes = Test*
python_functions = test_*
addopts =
    --strict-markers
    --disable-warnings
    --cov=mypackage
    --cov-report=term-missing
    --cov-report=html
markers =
    slow: marks tests as slow
    integration: marks tests as integration tests
    unit: marks tests as unit tests
```
