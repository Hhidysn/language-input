#include <windows.h>
#include <wincrypt.h>

#include <algorithm>
#include <atomic>
#include <chrono>
#include <condition_variable>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <mutex>
#include <string>
#include <thread>
#include <vector>

#include <boost/json.hpp>

#include "LanguageInputRemote.h"

#pragma comment(lib, "crypt32.lib")

namespace {

int failures = 0;

void Check(bool condition, const char* message) {
  if (!condition) {
    ++failures;
    std::cerr << "FAILED: " << message << '\n';
  }
}

weasel::language_input::RemoteGlossConfig TestConfig(
    const std::filesystem::path& cache) {
  weasel::language_input::RemoteGlossConfig config;
  config.enabled = true;
  config.endpoint = "https://example.invalid/v1/chat/completions";
  config.model = "test-model";
  config.api_key = "test-key";
  config.language = "en";
  config.cache_path = cache;
  return config;
}

void WriteCache(const std::filesystem::path& path,
                const std::string& language,
                const std::string& word,
                const std::string& gloss) {
  std::filesystem::create_directories(path.parent_path());
  boost::json::object entries;
  boost::json::object language_entries;
  language_entries[word] = gloss;
  entries[language] = std::move(language_entries);
  boost::json::object root;
  root["version"] = 2;
  root["model"] = "test-model";
  root["entries"] = std::move(entries);
  std::ofstream stream(path, std::ios::binary | std::ios::trunc);
  stream << boost::json::serialize(root);
}

}  // namespace

int wmain(int argc, wchar_t** argv) {
  if (argc > 1 && std::wstring(argv[1]) == L"--serve") {
    // Test-only child used to exercise the local-host worker path without
    // starting a real model runtime.
    std::this_thread::sleep_for(std::chrono::seconds(30));
    return 0;
  }

  if (argc == 4 &&
      (std::wstring(argv[1]) == L"--installed-smoke" ||
       std::wstring(argv[1]) == L"--installed-quickmt-warmup" ||
       std::wstring(argv[1]) == L"--installed-quickmt-cold")) {
    using namespace std::chrono_literals;
    using weasel::language_input::RemoteGlossConfig;
    using weasel::language_input::RemoteGlossError;
    using weasel::language_input::RemoteGlossService;

    const auto install_root = std::filesystem::absolute(argv[2]);
    const auto model_root = std::filesystem::absolute(argv[3]);
    const auto smoke_root =
        std::filesystem::absolute(L".cache") /
        (L"installed-remote-smoke-" + std::to_wstring(GetCurrentProcessId()));
    std::error_code smoke_error;
    std::filesystem::remove_all(smoke_root, smoke_error);
    std::filesystem::create_directories(smoke_root);

    const bool quickmt = std::wstring(argv[1]) != L"--installed-smoke";
    const bool warmup =
        std::wstring(argv[1]) == L"--installed-quickmt-warmup";
    RemoteGlossConfig config;
    config.enabled = true;
    config.use_local_host = true;
    config.model = quickmt ? "quickmt-gloss-route-v2" : "m2m100-418m-int8";
    config.language = "en";
    config.cache_path = smoke_root / L"cache.json";
    config.local_host_executable = install_root / L"LanguageInputModelHost.exe";
    config.local_host_catalog =
        install_root / L"data" / L"language_input" / L"models" /
        L"packs-v2.json";
    config.local_host_m2m100_catalog =
        install_root / L"data" / L"language_input" / L"models" /
        L"m2m100-packs-v1.json";
    config.local_host_models = model_root;

    std::atomic<int> completions = 0;
    {
      RemoteGlossService service(config, {}, [&](uintptr_t) { ++completions; });
      Check(service.available(), "the installed C++ smoke configuration is usable");
      service.SetSessionSensitive(42, false);
      if (warmup) {
        service.PrepareLocalModel(42, "en", config.model);
        Check(service.WaitUntilIdleForTesting(90s),
              "QuickMT background preload should finish");
      }
      const auto request_start = std::chrono::steady_clock::now();
      const std::vector<std::string> words = quickmt
          ? std::vector<std::string>{u8"你好", u8"世界", u8"学习", u8"工作",
                                     u8"今天", u8"明天", u8"朋友", u8"电脑",
                                     u8"语言"}
          : std::vector<std::string>{u8"你好"};
      service.QueueMissing(42, "en", words, config.model);
      Check(service.WaitUntilIdleForTesting(90s),
            "the installed C++ smoke request should finish");
      if (quickmt) {
        const auto elapsed = std::chrono::steady_clock::now() - request_start;
        std::cout << "QuickMT first request "
                  << (warmup ? "after warmup" : "without warmup") << ": "
                  << std::chrono::duration_cast<std::chrono::milliseconds>(
                         elapsed)
                         .count()
                  << " ms\n";
        if (warmup)
          Check(elapsed < 1s,
                "the prepared QuickMT request must finish within 1s");
      }
      auto gloss = service.Lookup(42, "en", u8"你好", config.model);
      Check(gloss && (quickmt ? !gloss->text.empty() : gloss->text == "Hello"),
            "the installed C++ smoke request should return the selected gloss");
      Check(service.TakeLastError(42) == RemoteGlossError::kNone,
            "the installed C++ smoke request should not report an error");
      Check(completions == 1,
            "the installed C++ smoke request should refresh the UI once");
    }
    std::filesystem::remove_all(smoke_root, smoke_error);
    if (failures == 0)
      std::cout << "LanguageInputRemoteInstalledSmoke: passed\n";
    return failures == 0 ? 0 : 1;
  }

  using namespace std::chrono_literals;
  using weasel::language_input::BuildRemoteGlossRequest;
  using weasel::language_input::ParseRemoteGlossResponse;
  using weasel::language_input::RemoteGlossMap;
  using weasel::language_input::RemoteGlossError;
  using weasel::language_input::RemoteGlossTransportResult;
  using weasel::language_input::RemoteGlossService;

  if (argc != 2) {
    std::cerr << "usage: LanguageInputRemoteTests <workspace-test-cache>\n";
    return 2;
  }
  std::filesystem::path test_root =
      std::filesystem::absolute(argv[1]) /
      (L"remote-gloss-" + std::to_wstring(GetCurrentProcessId()));
  std::error_code filesystem_error;
  std::filesystem::remove_all(test_root, filesystem_error);
  std::filesystem::create_directories(test_root);

  // A saved backend must survive a server launch without inherited settings
  // environment variables (the TSF recovery and login-start paths).
  wchar_t old_appdata[32768] = {};
  wchar_t old_enabled[128] = {};
  const DWORD old_appdata_size = GetEnvironmentVariableW(
      L"APPDATA", old_appdata, static_cast<DWORD>(std::size(old_appdata)));
  const DWORD old_enabled_size = GetEnvironmentVariableW(
      L"LANGUAGE_INPUT_REMOTE_ENABLED", old_enabled,
      static_cast<DWORD>(std::size(old_enabled)));
  const auto fake_appdata = test_root / L"appdata";
  const auto settings_dir = fake_appdata / L"LanguageInput";
  std::filesystem::create_directories(settings_dir);
  SetEnvironmentVariableW(L"APPDATA", fake_appdata.c_str());
  SetEnvironmentVariableW(L"LANGUAGE_INPUT_REMOTE_ENABLED", nullptr);
  const auto startup_cache = test_root / L"startup-cache.json";
  {
    std::ofstream file(settings_dir / L"config.json", std::ios::binary);
    file << R"({"backend":"off"})";
  }
  auto saved_off =
      weasel::language_input::LoadRemoteGlossConfig(startup_cache);
  Check(!saved_off.enabled && !saved_off.use_local_host,
        "a saved off choice must disable AI after a fresh server start");

  const std::string clear_key = "persisted-test-key";
  DATA_BLOB clear_blob{static_cast<DWORD>(clear_key.size()),
                       reinterpret_cast<BYTE*>(const_cast<char*>(clear_key.data()))};
  DATA_BLOB encrypted_blob{};
  if (CryptProtectData(&clear_blob, nullptr, nullptr, nullptr, nullptr,
                       CRYPTPROTECT_UI_FORBIDDEN, &encrypted_blob)) {
    DWORD encoded_size = 0;
    CryptBinaryToStringA(encrypted_blob.pbData, encrypted_blob.cbData,
                         CRYPT_STRING_BASE64 | CRYPT_STRING_NOCRLF, nullptr,
                         &encoded_size);
    std::string encoded(encoded_size, '\0');
    if (CryptBinaryToStringA(encrypted_blob.pbData, encrypted_blob.cbData,
                             CRYPT_STRING_BASE64 | CRYPT_STRING_NOCRLF,
                             encoded.data(), &encoded_size)) {
      encoded.resize(encoded_size);
      if (!encoded.empty() && encoded.back() == '\0')
        encoded.pop_back();
      boost::json::object settings;
      settings["backend"] = "remote";
      settings["remote_url"] = "https://example.invalid/v1/chat/completions";
      settings["remote_model"] = "persisted-model";
      settings["remote_language"] = "ja";
      settings["api_key_dpapi"] = encoded;
      std::ofstream file(settings_dir / L"config.json", std::ios::binary);
      file << boost::json::serialize(settings);
      file.close();
      auto saved_remote =
          weasel::language_input::LoadRemoteGlossConfig(startup_cache);
      Check(saved_remote.enabled && !saved_remote.use_local_host &&
                saved_remote.IsUsable() && saved_remote.api_key == clear_key &&
                saved_remote.model == "persisted-model" &&
                saved_remote.language == "ja",
            "a saved remote choice and DPAPI key must survive a fresh start");
      SetEnvironmentVariableW(L"LANGUAGE_INPUT_REMOTE_ENABLED", L"0");
      auto overridden =
          weasel::language_input::LoadRemoteGlossConfig(startup_cache);
      Check(!overridden.enabled,
            "an explicit environment override must take priority over settings");
    } else {
      Check(false, "DPAPI test key could not be base64-encoded");
    }
    LocalFree(encrypted_blob.pbData);
  } else {
    Check(false, "DPAPI test key could not be protected");
  }
  SetEnvironmentVariableW(L"APPDATA",
                          old_appdata_size ? old_appdata : nullptr);
  SetEnvironmentVariableW(L"LANGUAGE_INPUT_REMOTE_ENABLED",
                          old_enabled_size ? old_enabled : nullptr);

  auto config = TestConfig(test_root / L"request-cache.json");
  std::string request = BuildRemoteGlossRequest(config, {u8"缺词", u8"新词"});
  boost::system::error_code parse_error;
  auto request_json = boost::json::parse(request, parse_error);
  Check(!parse_error && request_json.is_object(),
        "the compatible chat request must be valid JSON");
  if (!parse_error && request_json.is_object()) {
    const auto& object = request_json.as_object();
    Check(object.at("model").as_string() == "test-model",
          "the selected compatible-API model must be sent");
    Check(object.at("messages").as_array().size() == 2,
          "the request must separate policy from untrusted word data");
  }

  const std::string response =
      R"({"choices":[{"message":{"content":"{\"缺词\":\"missing term\",\"extra\":\"ignore me\"}"}}]})";
  auto parsed = ParseRemoteGlossResponse(response, {u8"缺词"});
  Check(parsed && parsed->size() == 1 && parsed->at(u8"缺词") == "missing term",
        "only requested keys should survive response parsing");
  const std::string multiline =
      R"({"choices":[{"message":{"content":"{\"缺词\":\"bad\\nline\"}"}}]})";
  auto rejected = ParseRemoteGlossResponse(multiline, {u8"缺词"});
  Check(rejected && rejected->empty(),
        "multi-line model output must not enter a candidate comment");

  auto insecure = config;
  insecure.endpoint = "http://example.invalid/v1/chat/completions";
  Check(!insecure.IsUsable(), "plain HTTP must be rejected by default");
  insecure.allow_http = true;
  Check(insecure.IsUsable(), "plain HTTP requires an explicit opt-in");

  std::atomic<int> normal_calls = 0;
  std::atomic<int> completion_calls = 0;
  std::vector<std::string> received_words;
  auto normal_transport = [&](const auto&,
                              const std::vector<std::string>& words)
      -> RemoteGlossTransportResult {
    ++normal_calls;
    received_words = words;
    return {RemoteGlossMap{{u8"缺词", "missing term"}}, RemoteGlossError::kNone};
  };
  {
    RemoteGlossService service(
        TestConfig(test_root / L"normal" / L"cache.json"), normal_transport,
        [&](uintptr_t session_id) {
          if (session_id == 1)
            ++completion_calls;
        });
    service.SetSessionSensitive(1, false);
    Check(!service.Lookup(1, "en", u8"缺词"),
          "a miss should remain non-blocking before the worker finishes");
    service.QueueMissing(1, "en", {u8"缺词", u8"缺词"});
    Check(service.WaitUntilIdleForTesting(2s),
          "the fake normal request should finish");
    auto gloss = service.Lookup(1, "en", u8"缺词");
    Check(gloss && gloss->language == "en" && gloss->text == "missing term",
          "a normal async result should enter the cache");
    Check(normal_calls == 1 && received_words.size() == 1,
          "duplicate misses should be coalesced into one bounded request");
    Check(completion_calls == 1,
          "a current normal result should invoke one completion callback");
    Check(
        std::filesystem::is_regular_file(test_root / L"normal" / L"cache.json"),
        "a normal result should be persisted atomically");
  }

  std::vector<std::string> requested_languages;
  auto language_transport =
      [&](const auto& request_config,
          const std::vector<std::string>&) -> RemoteGlossTransportResult {
    requested_languages.push_back(request_config.language);
    return {RemoteGlossMap{{u8"文件夹",
                            request_config.language == "ja" ? u8"フォルダ"
                                                             : "folder"}},
            RemoteGlossError::kNone};
  };
  {
    RemoteGlossService service(
        TestConfig(test_root / L"languages" / L"cache.json"),
        language_transport);
    service.SetSessionSensitive(4, false);
    service.QueueMissing(4, "en", {u8"文件夹"});
    Check(service.WaitUntilIdleForTesting(2s),
          "the English cache-isolation request should finish");
    service.QueueMissing(4, "ja", {u8"文件夹"});
    Check(service.WaitUntilIdleForTesting(2s),
          "the Japanese cache-isolation request should finish");
    auto english = service.Lookup(4, "en", u8"文件夹");
    auto japanese = service.Lookup(4, "ja", u8"文件夹");
    Check(english && english->text == "folder" && japanese &&
              japanese->text == u8"フォルダ",
          "the same candidate must keep independent language cache entries");
    Check(requested_languages == std::vector<std::string>({"en", "ja"}),
          "each queued language must reach the transport independently");
  }

  std::vector<std::string> requested_models;
  auto model_transport =
      [&](const auto& request_config,
          const std::vector<std::string>&) -> RemoteGlossTransportResult {
    requested_models.push_back(request_config.model);
    return {RemoteGlossMap{{u8"模型词",
                            requested_models.size() == 1
                                ? "Quick gloss"
                                : "M2M100 gloss"}},
            RemoteGlossError::kNone};
  };
  {
    RemoteGlossService service(
        TestConfig(test_root / L"models" / L"cache.json"), model_transport);
    service.SetSessionSensitive(5, false);
    service.QueueMissing(5, "en", {u8"模型词"}, "quickmt-gloss-route-v2");
    Check(service.WaitUntilIdleForTesting(2s),
          "the QuickMT model request should finish");
    service.QueueMissing(5, "en", {u8"模型词"}, "m2m100-418m-int8");
    Check(service.WaitUntilIdleForTesting(2s),
          "the M2M100 model request should finish");
    auto quick = service.Lookup(5, "en", u8"模型词", "quickmt-gloss-route-v2");
    auto m2m100 = service.Lookup(5, "en", u8"模型词", "m2m100-418m-int8");
    Check(quick && quick->text == "Quick gloss" && m2m100 &&
              m2m100->text == "M2M100 gloss",
          "model selection must isolate cache entries");
    Check(requested_models == std::vector<std::string>({"test-model", "test-model"}),
          "legacy remote model selection must remain unchanged");
  }

  std::atomic<int> missing_completions = 0;
  auto missing_model_transport =
      [&](const auto&, const std::vector<std::string>&)
          -> RemoteGlossTransportResult {
    return {std::nullopt, RemoteGlossError::kMissingModel};
  };
  {
    RemoteGlossService service(
        TestConfig(test_root / L"missing" / L"cache.json"),
        missing_model_transport,
        [&](uintptr_t) { ++missing_completions; });
    service.SetSessionSensitive(6, false);
    service.QueueMissing(6, "en", {u8"未导入"}, "m2m100-418m-int8");
    Check(service.WaitUntilIdleForTesting(2s),
          "the missing-model request should finish without blocking");
    Check(service.TakeLastError(6) == RemoteGlossError::kMissingModel,
          "missing model must be surfaced as a typed error");
    Check(service.TakeLastError(6) == RemoteGlossError::kNone,
          "consuming a model error must clear it");
    Check(missing_completions == 1,
          "a missing model must refresh the UI with an explicit error");
  }

  std::atomic<int> local_transport_failures = 0;
  std::atomic<int> local_failure_completions = 0;
  auto local_failure_transport =
      [&](const auto&, const std::vector<std::string>&)
          -> RemoteGlossTransportResult {
    ++local_transport_failures;
    return {std::nullopt, RemoteGlossError::kTransport};
  };
  {
    const auto local_failure_root = test_root / L"local-transport-failure";
    std::filesystem::create_directories(local_failure_root / L"models");
    const auto local_failure_cache = local_failure_root / L"cache.json";
    {
      std::ofstream stream(local_failure_cache,
                           std::ios::binary | std::ios::trunc);
      stream << "{}";
    }
    auto local_config = TestConfig(local_failure_cache);
    local_config.use_local_host = true;
    local_config.model = "m2m100-418m-int8";
    local_config.local_host_executable =
        std::filesystem::absolute(std::filesystem::path(argv[0]));
    local_config.local_host_catalog = local_failure_cache;
    local_config.local_host_models = local_failure_root / L"models";
    RemoteGlossService service(
        local_config, local_failure_transport,
        [&](uintptr_t) { ++local_failure_completions; });
    Check(service.available(),
          "the local transport retry regression fixture must be usable");
    service.PrepareLocalModel(7, "en", "quickmt-gloss-route-v2");
    Check(service.WaitUntilIdleForTesting(100ms),
          "an unknown sensitive session must not start model preparation");
    service.SetSessionSensitive(7, false);
    service.PrepareLocalModel(7, "en", local_config.model);
    Check(service.WaitUntilIdleForTesting(100ms),
          "M2M100 selection must not start QuickMT preparation");
    service.QueueMissing(7, "en", {u8"本地失败"}, local_config.model);
    Check(service.WaitUntilIdleForTesting(2s),
          "a local transport failure must finish without repeated retries");
    Check(local_transport_failures == 1,
          "a local transport failure must issue exactly one request");
    Check(service.TakeLastError(7) == RemoteGlossError::kTransport,
          "a local transport failure must be surfaced to the UI");
    Check(local_failure_completions == 1,
          "a local transport failure must refresh the UI once");
  }

  const auto lazy_cache = test_root / L"lazy" / L"cache.json";
  WriteCache(lazy_cache, "en", u8"旧词", "old value");
  {
    RemoteGlossService service(TestConfig(lazy_cache), normal_transport);
    Check(!service.Lookup(2, "en", u8"旧词"),
          "unknown sessions must be sensitive by default");
    // If the sensitive lookup had read the file, this replacement would not
    // be observed because the service loads a cache only once.
    WriteCache(lazy_cache, "en", u8"新词", "new value");
    service.SetSessionSensitive(2, false);
    auto gloss = service.Lookup(2, "en", u8"新词");
    Check(gloss && gloss->text == "new value",
          "sensitive lookups must not read the disk cache");
  }

  std::mutex gate_mutex;
  std::condition_variable gate;
  bool started = false;
  bool release = false;
  auto delayed_transport =
      [&](const auto&,
          const std::vector<std::string>&) -> RemoteGlossTransportResult {
    std::unique_lock<std::mutex> lock(gate_mutex);
    started = true;
    gate.notify_all();
    gate.wait(lock, [&] { return release; });
    return {RemoteGlossMap{{u8"敏感词", "sensitive term"}},
            RemoteGlossError::kNone};
  };
  const auto dropped_cache = test_root / L"dropped" / L"cache.json";
  std::atomic<int> dropped_completions = 0;
  {
    RemoteGlossService service(TestConfig(dropped_cache), delayed_transport,
                               [&](uintptr_t) { ++dropped_completions; });
    service.SetSessionSensitive(3, false);
    const auto queue_start = std::chrono::steady_clock::now();
    service.QueueMissing(3, "en", {u8"敏感词"});
    const auto queue_elapsed = std::chrono::steady_clock::now() - queue_start;
    Check(queue_elapsed < 250ms,
          "queueing a remote miss must not block on the transport");
    {
      std::unique_lock<std::mutex> lock(gate_mutex);
      Check(gate.wait_for(lock, 2s, [&] { return started; }),
            "the delayed request should start");
    }
    service.SetSessionSensitive(3, true);
    {
      std::lock_guard<std::mutex> lock(gate_mutex);
      release = true;
    }
    gate.notify_all();
    Check(service.WaitUntilIdleForTesting(2s),
          "the invalidated request should finish and be discarded");
    service.SetSessionSensitive(3, false);
    Check(!service.Lookup(3, "en", u8"敏感词"),
          "a result completed after the sensitive transition must be dropped");
    Check(!std::filesystem::exists(dropped_cache),
          "a dropped sensitive result must not write a cache file");
    Check(dropped_completions == 0,
          "a result invalidated by sensitive mode must not refresh the UI");
  }

  std::mutex queue_mutex;
  std::condition_variable queue_gate;
  bool first_started = false;
  bool release_first = false;
  std::vector<std::string> requested_words;
  auto queued_transport = [&](const auto&, const std::vector<std::string>& words)
      -> RemoteGlossTransportResult {
    {
      std::unique_lock<std::mutex> lock(queue_mutex);
      requested_words.insert(requested_words.end(), words.begin(), words.end());
      if (words.front() == u8"首词") {
        first_started = true;
        queue_gate.notify_all();
        queue_gate.wait(lock, [&] { return release_first; });
      }
    }
    RemoteGlossMap glosses;
    for (const auto& word : words)
      glosses[word] = "gloss";
    return {std::move(glosses), RemoteGlossError::kNone};
  };
  {
    RemoteGlossService service(
        TestConfig(test_root / L"latest" / L"cache.json"), queued_transport);
    service.SetSessionSensitive(11, false);
    service.QueueMissing(11, "en", {u8"首词"});
    {
      std::unique_lock<std::mutex> lock(queue_mutex);
      Check(queue_gate.wait_for(lock, 2s, [&] { return first_started; }),
            "the first candidate page should start inference");
    }
    service.QueueMissing(11, "en", {u8"过期词"});
    service.QueueMissing(11, "en", {u8"当前词"});
    {
      std::lock_guard<std::mutex> lock(queue_mutex);
      release_first = true;
    }
    queue_gate.notify_all();
    Check(service.WaitUntilIdleForTesting(2s),
          "the latest candidate page should finish");
    Check(std::find(requested_words.begin(), requested_words.end(), u8"过期词") ==
              requested_words.end(),
          "a superseded candidate page must not reach the model");
    Check(service.Lookup(11, "en", u8"当前词").has_value(),
          "the latest candidate page should enter the cache");
  }

  std::filesystem::remove_all(test_root, filesystem_error);
  if (failures == 0)
    std::cout << "LanguageInputRemoteTests: all checks passed\n";
  return failures == 0 ? 0 : 1;
}
