#pragma once

#include <chrono>
#include <cstdint>
#include <filesystem>
#include <functional>
#include <map>
#include <memory>
#include <optional>
#include <string>
#include <string_view>
#include <vector>

namespace weasel::language_input {

struct RemoteGloss {
  std::string language;
  std::string text;

  std::string MarkedComment() const;
};

struct RemoteGlossConfig {
  bool enabled = false;
  bool allow_http = false;
  std::string endpoint = "https://api.openai.com/v1/chat/completions";
  std::string model = "gpt-4.1-mini";
  std::string api_key;
  std::string language = "en";
  std::filesystem::path cache_path;
  int connect_timeout_ms = 5000;
  int request_timeout_ms = 15000;

  bool IsUsable() const;
};

using RemoteGlossMap = std::map<std::string, std::string>;
using RemoteGlossTransport = std::function<std::optional<RemoteGlossMap>(
    const RemoteGlossConfig&,
    const std::vector<std::string>&)>;

// Remote access is disabled unless LANGUAGE_INPUT_REMOTE_ENABLED is true.
// The API key is read only in that case and is never written to configuration.
RemoteGlossConfig LoadRemoteGlossConfig(
    const std::filesystem::path& cache_path);

// Exposed for deterministic parser tests and compatible-API diagnostics.
std::string BuildRemoteGlossRequest(const RemoteGlossConfig& config,
                                    const std::vector<std::string>& words);
std::optional<RemoteGlossMap> ParseRemoteGlossResponse(
    std::string_view response,
    const std::vector<std::string>& requested_words);

class RemoteGlossService {
 public:
  explicit RemoteGlossService(RemoteGlossConfig config,
                              RemoteGlossTransport transport = {});
  ~RemoteGlossService();

  RemoteGlossService(const RemoteGlossService&) = delete;
  RemoteGlossService& operator=(const RemoteGlossService&) = delete;

  bool available() const;

  // Unknown sessions are sensitive by default. Call this with false only
  // after the platform input-scope check has completed.
  void SetSessionSensitive(uintptr_t session_id, bool sensitive);
  void RemoveSession(uintptr_t session_id);
  void SuspendAllSessions();

  std::optional<RemoteGloss> Lookup(uintptr_t session_id,
                                    std::string_view word);
  void QueueMissing(uintptr_t session_id,
                    const std::vector<std::string>& words);

  // Used by the native test executable; production never waits for requests.
  bool WaitUntilIdleForTesting(std::chrono::milliseconds timeout);

 private:
  class Impl;
  std::unique_ptr<Impl> impl_;
};

}  // namespace weasel::language_input
