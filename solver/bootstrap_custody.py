"""One-way bootstrap transfer into owner-specific broker processes."""

from __future__ import annotations

import os
import stat
from collections.abc import Callable, Mapping, MutableMapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from solver.broker_contracts import Broker, BrokerReceipt, transfer_digest
from solver.broker_vault import BrokerVaultProcess
from solver.capability_event_contracts import BrokerCustody
from solver.credentials import CHILD_ENVIRONMENT
from solver.event_store import EventStore
from solver.event_store_contracts import CapabilityCustodyRecorded, CapabilityRecord
from solver.redaction import Redactor


class SecretSource:
    """Mutable bootstrap bytes and the one source reference they must clear."""

    def __init__(
        self,
        name: str,
        owner: Broker,
        material: bytearray,
        *,
        environment: MutableMapping[str, str] | None = None,
        temporary_path: Path | None = None,
        temporary_identity: tuple[int, int] | None = None,
    ) -> None:
        if not name or not material:
            raise ValueError("a bootstrap secret needs a name and nonempty material")
        self.name = name
        self.owner = Broker(owner)
        self._material = material
        self._environment = environment
        self._temporary_path = temporary_path
        self._temporary_identity = temporary_identity
        self.cleared = False

    @classmethod
    def from_environment(
        cls,
        environment: MutableMapping[str, str],
        name: str,
        owner: Broker,
    ) -> SecretSource:
        value = environment.get(name, "")
        if not value:
            raise ValueError(f"bootstrap environment has no nonempty {name}")
        return cls(name, owner, bytearray(value.encode()), environment=environment)

    @classmethod
    def from_temporary_file(cls, path: Path, name: str, owner: Broker) -> SecretSource:
        source = Path(path)
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(source, flags)
        try:
            metadata = os.fstat(descriptor)
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_mode & 0o077 or metadata.st_uid != os.getuid():
                raise PermissionError("bootstrap secret file must be private and regular")
            material = bytearray(metadata.st_size)
            with os.fdopen(descriptor, "rb", closefd=False) as stream:
                if stream.readinto(material) != metadata.st_size:
                    raise OSError("bootstrap secret file changed while it was read")
        finally:
            os.close(descriptor)
        return cls(
            name,
            owner,
            material,
            temporary_path=source,
            temporary_identity=(metadata.st_dev, metadata.st_ino),
        )

    @property
    def zeroed(self) -> bool:
        return bool(self._material) and not any(self._material)

    def bytes_for_transfer(self) -> bytearray:
        if self.cleared:
            raise RuntimeError(f"bootstrap source {self.name} is already cleared")
        return self._material

    def clear(self) -> None:
        if self.cleared:
            return
        for index in range(len(self._material)):
            self._material[index] = 0
        if self._environment is not None:
            # Replacing before deletion avoids leaving the original value in the live mapping.
            self._environment[self.name] = ""
            del self._environment[self.name]
        if self._temporary_path is not None:
            descriptor = os.open(
                self._temporary_path,
                os.O_WRONLY | getattr(os, "O_NOFOLLOW", 0),
            )
            try:
                metadata = os.fstat(descriptor)
                if (metadata.st_dev, metadata.st_ino) != self._temporary_identity:
                    raise PermissionError("bootstrap secret file changed before clear")
                remaining = len(self._material)
                zeroes = bytes(min(remaining, 65536))
                while remaining:
                    written = os.write(descriptor, zeroes[:remaining])
                    remaining -= written
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
            self._temporary_path.unlink(missing_ok=True)
            _fsync_directory(self._temporary_path.parent)
        self.cleared = True


@dataclass
class BootstrapResult:
    executor_environment: dict[str, str]
    brokers: dict[Broker, BrokerVaultProcess]
    receipts: dict[Broker, BrokerReceipt]
    endpoints: dict[Broker, Path] = field(default_factory=dict)

    @property
    def holdings(self) -> dict[Broker, tuple[str, ...]]:
        return {owner: receipt.secret_names for owner, receipt in self.receipts.items()}

    def close(self) -> None:
        first_error = None
        for broker in self.brokers.values():
            try:
                broker.close()
            except Exception as error:
                first_error = first_error or error
        if first_error is not None:
            raise first_error


class BootstrapCustody:
    """Transfer all declared sources, clear custody, and leave only opaque broker processes."""

    def __init__(
        self,
        *,
        state: Path,
        run_id: str,
        boot_id: str,
        redactor: Redactor,
        timestamp: Callable[[], str],
        start_broker: Callable[
            [Broker, Mapping[str, bytearray]], tuple[BrokerVaultProcess, BrokerReceipt]
        ] = BrokerVaultProcess.start,
    ) -> None:
        self._run_id = run_id
        self._boot_id = boot_id
        self._store = EventStore(state, run_id=run_id, redactor=redactor)
        self._timestamp = timestamp
        self._start_broker = start_broker
        self._audit_serial = sum(event.event_type == "capability-custody.recorded" for event in self._store.events())

    def transfer(
        self,
        sources: Sequence[SecretSource],
        environment: MutableMapping[str, str],
    ) -> BootstrapResult:
        if not sources or len({source.name for source in sources}) != len(sources):
            raise ValueError("bootstrap secret names must be unique and nonempty")
        grouped: dict[Broker, dict[str, bytearray]] = {}
        for source in sources:
            grouped.setdefault(source.owner, {})[source.name] = source.bytes_for_transfer()
        brokers: dict[Broker, BrokerVaultProcess] = {}
        receipts: dict[Broker, BrokerReceipt] = {}
        try:
            for owner in Broker:
                if owner not in grouped:
                    continue
                broker, receipt = self._start_broker(owner, grouped[owner])
                brokers[owner] = broker
                receipts[owner] = receipt
                self._record_transfer(CapabilityRecord.TRANSFER_ACCEPTED, receipt)
            for source in sources:
                source.clear()
                self._record_source_cleared(source, receipts[source.owner])
        except Exception:
            for source in sources:
                try:
                    source.clear()
                except OSError:
                    pass
            for broker in brokers.values():
                try:
                    broker.close()
                except OSError:
                    pass
            raise
        executor_environment = {name: environment[name] for name in CHILD_ENVIRONMENT if name in environment}
        return BootstrapResult(executor_environment, brokers, receipts)

    def _record_transfer(self, record: CapabilityRecord, receipt: BrokerReceipt) -> None:
        self._audit_serial += 1
        self._store.append(
            CapabilityCustodyRecorded(
                event_id=self._event_id(),
                fact=BrokerCustody(
                    record=record,
                    run_id=self._run_id,
                    boot_id=self._boot_id,
                    broker=receipt.owner.value,
                    secret_names=receipt.secret_names,
                    evidence_digest=receipt.transfer_digest,
                    peer_uid=receipt.uid,
                    peer_identity_digest=receipt.identity_digest,
                ),
                ts=self._timestamp(),
            ),
            body=b"",
        )

    def _record_source_cleared(self, source: SecretSource, receipt: BrokerReceipt) -> None:
        self._audit_serial += 1
        self._store.append(
            CapabilityCustodyRecorded(
                event_id=self._event_id(),
                fact=BrokerCustody(
                    record=CapabilityRecord.SOURCE_CLEARED,
                    run_id=self._run_id,
                    boot_id=self._boot_id,
                    broker=source.owner.value,
                    secret_names=(source.name,),
                    evidence_digest=receipt.transfer_digest,
                    peer_uid=receipt.uid,
                    peer_identity_digest=receipt.identity_digest,
                ),
                ts=self._timestamp(),
            ),
            body=b"",
        )

    def _event_id(self) -> str:
        return f"capability:{self._boot_id}:{self._audit_serial:06d}"


def _fsync_directory(directory: Path) -> None:
    try:
        descriptor = os.open(directory, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(descriptor)
    except OSError:
        pass
    finally:
        os.close(descriptor)


__all__ = [
    "BootstrapCustody",
    "BootstrapResult",
    "Broker",
    "BrokerReceipt",
    "BrokerVaultProcess",
    "SecretSource",
    "transfer_digest",
]
