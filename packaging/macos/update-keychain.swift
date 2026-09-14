// Local release helper. Secret bytes travel through pipes, never command arguments.
import Foundation
import Security

SecKeychainSetUserInteractionAllowed(false)
let arguments = CommandLine.arguments
guard arguments.count == 4 else { exit(64) }
var query: [String: Any] = [
    kSecClass as String: kSecClassGenericPassword,
    kSecAttrService as String: arguments[2],
    kSecAttrAccount as String: arguments[3]
]
let status: OSStatus
switch arguments[1] {
case "store":
    let data = FileHandle.standardInput.readDataToEndOfFile()
    guard !data.isEmpty else { exit(65) }
    query[kSecValueData as String] = data
    query[kSecAttrLabel as String] = "LocalSR production update signing"
    status = SecItemAdd(query as CFDictionary, nil)
case "read":
    query[kSecReturnData as String] = true
    query[kSecMatchLimit as String] = kSecMatchLimitOne
    var result: CFTypeRef?
    status = SecItemCopyMatching(query as CFDictionary, &result)
    if status == errSecSuccess, let data = result as? Data {
        FileHandle.standardOutput.write(data)
    }
default:
    exit(64)
}
if status == errSecItemNotFound { exit(44) }
if status != errSecSuccess {
    FileHandle.standardError.write(Data("Keychain operation failed (OSStatus \(status)).\n".utf8))
    exit(1)
}
