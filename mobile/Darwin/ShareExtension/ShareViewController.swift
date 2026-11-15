import UIKit
import UniformTypeIdentifiers

@objc(ShareViewController)
final class ShareViewController: UIViewController {
    private static let appGroupID = "group.com.hermesreisner.localsr"
    private static let importURL = URL(string: "localsr://import")!
    private static let maxSharedItems = 20

    private struct Manifest: Encodable {
        struct Item: Encodable {
            let filename: String
            let displayName: String?
        }

        let version: Int
        let createdAt: String
        let items: [Item]
    }

    private let titleLabel = UILabel()
    private let statusLabel = UILabel()
    private let spinner = UIActivityIndicatorView(style: .medium)
    private let doneButton = UIButton(type: .system)
    private var didStartImport = false

    override func viewDidLoad() {
        super.viewDidLoad()
        setupUI()
    }

    override func viewDidAppear(_ animated: Bool) {
        super.viewDidAppear(animated)
        guard !didStartImport else { return }
        didStartImport = true
        Task { @MainActor in
            await processSharedItems()
        }
    }

    private func setupUI() {
        view.backgroundColor = UIColor.black.withAlphaComponent(0.28)

        let card = UIVisualEffectView(effect: UIBlurEffect(style: .systemMaterial))
        card.layer.cornerRadius = 24
        card.clipsToBounds = true
        card.translatesAutoresizingMaskIntoConstraints = false

        titleLabel.text = "Upscale with LocalSR"
        titleLabel.font = UIFont.preferredFont(forTextStyle: .headline)
        titleLabel.textAlignment = .center
        titleLabel.adjustsFontForContentSizeCategory = true

        statusLabel.text = "Preparing photos\u{2026}"
        statusLabel.font = UIFont.preferredFont(forTextStyle: .subheadline)
        statusLabel.textColor = .secondaryLabel
        statusLabel.textAlignment = .center
        statusLabel.numberOfLines = 0
        statusLabel.adjustsFontForContentSizeCategory = true

        spinner.startAnimating()

        var config = UIButton.Configuration.filled()
        config.title = "OK"
        config.cornerStyle = .capsule
        doneButton.configuration = config
        doneButton.isHidden = true
        doneButton.addTarget(self, action: #selector(dismissExtension), for: .touchUpInside)

        let stack = UIStackView(arrangedSubviews: [spinner, titleLabel, statusLabel, doneButton])
        stack.axis = .vertical
        stack.alignment = .center
        stack.spacing = 10
        stack.translatesAutoresizingMaskIntoConstraints = false

        card.contentView.addSubview(stack)
        view.addSubview(card)

        NSLayoutConstraint.activate([
            card.centerXAnchor.constraint(equalTo: view.centerXAnchor),
            card.centerYAnchor.constraint(equalTo: view.centerYAnchor),
            card.widthAnchor.constraint(lessThanOrEqualToConstant: 300),
            card.leadingAnchor.constraint(greaterThanOrEqualTo: view.leadingAnchor, constant: 24),
            card.trailingAnchor.constraint(lessThanOrEqualTo: view.trailingAnchor, constant: -24),

            stack.topAnchor.constraint(equalTo: card.contentView.topAnchor, constant: 22),
            stack.bottomAnchor.constraint(equalTo: card.contentView.bottomAnchor, constant: -22),
            stack.leadingAnchor.constraint(equalTo: card.contentView.leadingAnchor, constant: 24),
            stack.trailingAnchor.constraint(equalTo: card.contentView.trailingAnchor, constant: -24),
        ])
    }

    @objc private func dismissExtension() {
        extensionContext?.completeRequest(returningItems: nil)
    }

    private func processSharedItems() async {
        let providers = imageProviders()
        guard !providers.isEmpty else {
            extensionContext?.completeRequest(returningItems: nil)
            return
        }

        statusLabel.text = providers.count == 1
            ? "Sending 1 photo\u{2026}"
            : "Sending \(providers.count) photos\u{2026}"

        guard let containerURL = FileManager.default.containerURL(forSecurityApplicationGroupIdentifier: Self.appGroupID) else {
            showFallback(message: "Could not access LocalSR storage.")
            return
        }

        let batchDir = containerURL
            .appendingPathComponent("Inbox", isDirectory: true)
            .appendingPathComponent(UUID().uuidString, isDirectory: true)

        do {
            try FileManager.default.createDirectory(at: batchDir, withIntermediateDirectories: true)
        } catch {
            showFallback(message: "Could not prepare LocalSR inbox.")
            return
        }

        var manifestItems: [Manifest.Item] = []
        for provider in providers {
            if let item = await copyImageRepresentation(from: provider, into: batchDir) {
                manifestItems.append(item)
            }
        }

        guard !manifestItems.isEmpty else {
            try? FileManager.default.removeItem(at: batchDir)
            showFallback(message: "No supported photos found.")
            return
        }

        let manifest = Manifest(
            version: 1,
            createdAt: ISO8601DateFormatter().string(from: Date()),
            items: manifestItems
        )
        let manifestURL = batchDir.appendingPathComponent("manifest.json")
        do {
            let data = try JSONEncoder().encode(manifest)
            try data.write(to: manifestURL, options: .atomic)
        } catch {
            try? FileManager.default.removeItem(at: batchDir)
            showFallback(message: "Could not save photo queue.")
            return
        }

        let opened = await openContainingApp(url: Self.importURL)
        if opened {
            extensionContext?.completeRequest(returningItems: nil)
        } else {
            showFallback(message: "Queued for LocalSR. Open LocalSR to upscale.")
        }
    }

    private func showFallback(message: String) {
        spinner.stopAnimating()
        spinner.isHidden = true
        statusLabel.text = message
        doneButton.isHidden = false
    }

    private func imageProviders() -> [NSItemProvider] {
        guard let extensionItems = extensionContext?.inputItems as? [NSExtensionItem] else {
            return []
        }
        var result: [NSItemProvider] = []
        for item in extensionItems {
            for provider in item.attachments ?? [] {
                if provider.hasItemConformingToTypeIdentifier(UTType.image.identifier) {
                    result.append(provider)
                    if result.count >= Self.maxSharedItems {
                        return result
                    }
                }
            }
        }
        return result
    }

    private func copyImageRepresentation(from provider: NSItemProvider, into batchDir: URL) async -> Manifest.Item? {
        let fallbackExt = Self.preferredExtension(for: provider)
        let displayName = provider.suggestedName

        return await withCheckedContinuation { continuation in
            provider.loadFileRepresentation(forTypeIdentifier: UTType.image.identifier) { tempURL, _ in
                guard let tempURL else {
                    continuation.resume(returning: nil)
                    return
                }
                let ext = tempURL.pathExtension.isEmpty ? fallbackExt : tempURL.pathExtension
                let filename = ext.isEmpty ? UUID().uuidString : "\(UUID().uuidString).\(ext)"
                let destURL = batchDir.appendingPathComponent(filename)
                do {
                    try FileManager.default.copyItem(at: tempURL, to: destURL)
                    continuation.resume(returning: Manifest.Item(filename: filename, displayName: displayName))
                } catch {
                    continuation.resume(returning: nil)
                }
            }
        }
    }

    private static func preferredExtension(for provider: NSItemProvider) -> String {
        for identifier in provider.registeredTypeIdentifiers {
            if let type = UTType(identifier), type.conforms(to: .image), let ext = type.preferredFilenameExtension {
                return ext
            }
        }
        return "jpg"
    }

    private func openContainingApp(url: URL) async -> Bool {
        var responder: UIResponder? = self
        while let current = responder {
            if let application = current as? UIApplication {
                return await withCheckedContinuation { continuation in
                    application.open(url, options: [:]) { success in
                        continuation.resume(returning: success)
                    }
                }
            }
            responder = current.next
        }
        return false
    }
}
