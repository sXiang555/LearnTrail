package com.learntrail.media.service;

import com.learntrail.api.client.course.CourseClient;
import com.learntrail.common.exceptions.*;
import com.learntrail.common.utils.UserContext;
import com.learntrail.media.config.MediaUploadProperties;
import com.learntrail.media.domain.dto.MediaUploadModels.*;
import com.learntrail.media.domain.po.Media;
import com.learntrail.media.enums.FileStatus;
import com.learntrail.media.mapper.*;
import com.learntrail.media.storage.MultipartStorage;
import org.junit.jupiter.api.*;
import java.time.*;
import java.util.*;
import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.ArgumentMatchers.*;
import static org.mockito.Mockito.*;

class MediaUploadServiceTest {
    private final MediaUploadProperties config = new MediaUploadProperties();
    private final MediaUploadMapper mapper = mock(MediaUploadMapper.class);
    private final MediaMapper media = mock(MediaMapper.class);
    private final CourseClient courses = mock(CourseClient.class);
    private final MultipartStorage storage = mock(MultipartStorage.class);
    private final MediaUploadStore store = mock(MediaUploadStore.class);
    private final MediaUploadService service = new MediaUploadService(config, mapper, media, courses, storage, store);
    private Session session;
    private final String id = "a".repeat(32);

    @BeforeEach
    void setup() {
        UserContext.setUser(7L); config.setEnabled(true);
        session = new Session(); session.setId(id); session.setUserId(7L); session.setFilename("video.mp4");
        session.setRequestKey("request-key"); session.setSha256("b".repeat(64)); session.setFileSize(15);
        session.setPartSize(10); session.setPartCount(2); session.setPhase("UPLOADING");
        session.setExpiresAt(LocalDateTime.now(ZoneOffset.UTC).plusHours(1));
        when(mapper.owned(id, 7L)).thenReturn(session); when(courses.studioAccess()).thenReturn(true);
    }
    @AfterEach void cleanup() { UserContext.removeUser(); }

    private Init form() {
        Init value = new Init(); value.setRequestKey("request-key"); value.setFilename("video.mp4");
        value.setSize(15); value.setSha256("b".repeat(64)); return value;
    }
    private List<Part> parts() { return new ArrayList<>(List.of(new Part(1, "\"first\"", 10), new Part(2, "\"last\"", 5))); }

    @Test void anonymousCannotStartOrGetCapabilities() {
        UserContext.removeUser();
        assertThrows(UnauthorizedException.class, () -> service.initiate(form()));
        assertThrows(UnauthorizedException.class, service::capabilities);
        verifyNoInteractions(mapper, storage);
    }
    @Test void disabledFeatureDoesNotAccessNewTables() {
        config.setEnabled(false);
        assertThrows(BadRequestException.class, () -> service.initiate(form()));
        verifyNoInteractions(mapper, storage);
    }
    @Test void creatorAccessIsCheckedBeforeSessionCreation() {
        when(courses.studioAccess()).thenReturn(false);
        assertThrows(ForbiddenException.class, () -> service.initiate(form()));
        verifyNoInteractions(mapper, storage);
    }
    @Test void foreignOwnerCannotSignCompleteOrAbort() {
        UserContext.setUser(8L);
        assertThrows(BadRequestException.class, () -> service.sign(id, 1));
        assertThrows(BadRequestException.class, () -> service.complete(id));
        assertThrows(BadRequestException.class, () -> service.abort(id));
        verifyNoInteractions(storage, store);
    }
    @Test void sameRequestCannotChangeFileHash() {
        when(mapper.request(7L, "request-key")).thenReturn(session);
        Init different = form(); different.setSha256("c".repeat(64));
        assertThrows(BadRequestException.class, () -> service.initiate(different));
        verify(mapper, never()).insert(any()); verifyNoInteractions(storage);
    }
    @Test void expiredSessionAndOutOfRangePartsCannotObtainSignature() {
        assertThrows(BadRequestException.class, () -> service.sign(id, 3));
        session.setExpiresAt(LocalDateTime.now(ZoneOffset.UTC).minusSeconds(1));
        assertThrows(BadRequestException.class, () -> service.sign(id, 1)); verifyNoInteractions(storage);
    }
    @Test void lastPartSignatureUsesRemainingBytes() {
        when(storage.signPart(session, 2)).thenReturn("http://storage/part");
        assertEquals(5, service.sign(id, 2).getSize());
    }
    @Test void missingPartsNeverMergeOrCreateAsset() {
        when(mapper.claimComplete(eq(id), anyString())).thenReturn(1);
        when(storage.parts(session)).thenReturn(List.of(new Part(1, "first", 10)));
        assertThrows(BadRequestException.class, () -> service.complete(id));
        verify(mapper).incomplete(eq(id), anyString(), eq("MISSING_OR_INVALID_PARTS"));
        verify(storage, never()).complete(any(), any()); verifyNoInteractions(store);
    }
    @Test void forgedEtagCannotCompleteUpload() {
        List<Part> reported = parts(); reported.get(1).setEtag("wrong");
        assertThrows(BadRequestException.class, () -> MediaUploadService.verifyParts(session, reported, parts()));
    }
    @Test void duplicatePartNumberAndWrongSizeAreRejected() {
        List<Part> actual = parts(); actual.get(1).setNumber(1);
        assertThrows(BadRequestException.class, () -> MediaUploadService.verifyParts(session, parts(), actual));
        actual.get(1).setNumber(2); actual.get(1).setSize(4);
        assertThrows(BadRequestException.class, () -> MediaUploadService.verifyParts(session, parts(), actual));
    }
    @Test void validReceiptsHandleQuotedEtags() {
        List<Part> reported = parts(); reported.get(0).setEtag("first");
        assertDoesNotThrow(() -> MediaUploadService.verifyParts(session, reported, parts()));
    }
    @Test void unknownMergeOutcomeRemainsRecoverable() {
        when(mapper.claimComplete(eq(id), anyString())).thenReturn(1);
        when(mapper.parts(id)).thenReturn(parts()); when(storage.parts(session)).thenReturn(parts());
        doThrow(new IllegalStateException("network interrupted")).when(storage).complete(eq(session), anyList());
        assertThrows(BadRequestException.class, () -> service.complete(id));
        verify(mapper, never()).incomplete(anyString(), anyString(), anyString()); verifyNoInteractions(store);
    }
    @Test void alreadyMergedObjectRegistersWithoutUploadingAgain() {
        when(mapper.claimComplete(eq(id), anyString())).thenReturn(1);
        when(storage.completed(session)).thenReturn(true);
        service.complete(id);
        verify(store).attach(eq(session), anyString()); verify(storage, never()).complete(any(), any());
    }
    @Test void resumeUsesObjectStoragePartsEvenWhenReceiptWasLost() {
        when(storage.parts(session)).thenReturn(parts());
        View view = service.status(id);
        assertEquals(2, view.getParts().size());
        verify(store, times(2)).rememberPart(eq(id), eq(7L), any(Part.class));
    }
    @Test void deduplicationOnlyLooksUpCurrentOwnersVerifiedMedia() {
        when(mapper.verified(7L, "b".repeat(64), 15)).thenReturn(90L);
        Media saved = new Media().setId(90L).setCreater(7L).setFilename("video.mp4").setDeleted(0)
                .setStatus(FileStatus.PROCESSED).setDuration(2F);
        when(media.selectById(90L)).thenReturn(saved);
        assertTrue(service.initiate(form()).getAsset().isReady());
        verify(mapper, never()).insert(any()); verifyNoInteractions(storage);
    }
}
