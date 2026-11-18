package com.learntrail.media.service;

import com.learntrail.media.config.MediaUploadProperties;
import com.learntrail.media.domain.dto.MediaUploadModels.*;
import com.learntrail.media.mapper.*;
import com.rabbitmq.client.Channel;
import org.junit.jupiter.api.*;
import org.mockito.InOrder;
import java.util.concurrent.*;
import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.ArgumentMatchers.*;
import static org.mockito.Mockito.*;

class MediaTaskWorkerTest {
    private final MediaTaskMapper tasks = mock(MediaTaskMapper.class);
    private final MediaUploadMapper uploads = mock(MediaUploadMapper.class);
    private final MediaUploadStore store = mock(MediaUploadStore.class);
    private final MediaTranscoder transcoder = mock(MediaTranscoder.class);
    private final MediaUploadProperties config = new MediaUploadProperties();
    private final ThreadPoolExecutor pool = new ThreadPoolExecutor(1, 1, 0, TimeUnit.SECONDS,
            new ArrayBlockingQueue<>(1), new ThreadPoolExecutor.AbortPolicy());
    private final ScheduledExecutorService leases = Executors.newSingleThreadScheduledExecutor();
    private final Channel channel = mock(Channel.class);
    private final MediaTaskWorker worker = new MediaTaskWorker(tasks, uploads, store, transcoder, config, pool, leases);
    private Task task;
    private Session session;
    private Output output;

    @BeforeEach void setup() throws Exception {
        task = new Task(); task.setId(1L); task.setMediaId(10L); task.setUploadId("a".repeat(32));
        session = new Session(); session.setId(task.getUploadId()); session.setMediaId(10L);
        output = new Output(); output.setDuration(2); output.setSize(100);
        when(tasks.find(1L)).thenReturn(task);
        when(tasks.claim(eq(1L), anyString(), anyInt(), anyInt())).thenAnswer(call -> {
            task.setRunToken(call.getArgument(1)); task.setAttempts(1); return 1;
        });
        when(tasks.processing(eq(1L), anyString())).thenReturn(1);
        when(uploads.find(task.getUploadId())).thenReturn(session);
        when(transcoder.process(eq(session), anyString(), any())).thenReturn(output);
    }
    @AfterEach void cleanup() { pool.shutdownNow(); leases.shutdownNow(); }

    @Test void successCommitsResultBeforeAcknowledgement() throws Exception {
        worker.consume(1L, channel, 9);
        InOrder order = inOrder(store, channel);
        order.verify(store).succeeded(task, session, output);
        order.verify(channel).basicAck(9, false);
        verify(store, never()).failed(any(), anyString(), anyInt(), anyBoolean());
    }
    @Test void duplicateTaskCannotRunAgain() throws Exception {
        when(tasks.claim(eq(1L), anyString(), anyInt(), anyInt())).thenReturn(0);
        worker.consume(1L, channel, 9);
        verifyNoInteractions(transcoder, store); verify(channel).basicAck(9, false);
    }
    @Test void permanentHashFailureIsRecordedBeforeAck() throws Exception {
        when(transcoder.process(eq(session), anyString(), any())).thenThrow(new MediaTranscoder.Failure("SOURCE_HASH_MISMATCH", true));
        worker.consume(1L, channel, 9);
        InOrder order = inOrder(store, channel);
        order.verify(store).failed(task, "SOURCE_HASH_MISMATCH", 3, true);
        order.verify(channel).basicAck(9, false);
        verify(store, never()).succeeded(any(), any(), any());
    }
    @Test void transientFailureSchedulesRecoveryWithoutSuccessfulResult() throws Exception {
        when(transcoder.process(eq(session), anyString(), any())).thenThrow(new java.io.IOException("storage unavailable"));
        worker.consume(1L, channel, 9);
        verify(store).failed(task, "MEDIA_PROCESS_RETRY", 3, false);
        verify(store, never()).succeeded(any(), any(), any()); verify(channel).basicAck(9, false);
    }
    @Test void persistenceFailureMustNotAcknowledgeAsSuccessful() throws Exception {
        doThrow(new IllegalStateException("database unavailable")).when(store).succeeded(any(), any(), any());
        doThrow(new IllegalStateException("database unavailable")).when(store).failed(any(), anyString(), anyInt(), anyBoolean());
        worker.consume(1L, channel, 9);
        verify(channel, never()).basicAck(anyLong(), anyBoolean()); verify(channel).basicNack(9, false, false);
    }
    @Test void unknownTaskIsDeadLetteredWithoutExecution() throws Exception {
        when(tasks.find(1L)).thenReturn(null);
        worker.consume(1L, channel, 9);
        verify(channel).basicNack(9, false, false); verifyNoInteractions(transcoder, store);
    }
    @Test void foreignOrMissingMediaSourceCannotProduceResult() throws Exception {
        session.setMediaId(11L);
        worker.consume(1L, channel, 9);
        verify(store).failed(task, "TASK_SOURCE_INVALID", 3, true); verifyNoInteractions(transcoder);
    }
    @Test void boundedPoolRejectsOverloadWithoutClaimingTask() throws Exception {
        CountDownLatch started = new CountDownLatch(1), release = new CountDownLatch(1);
        pool.execute(() -> {
            started.countDown();
            try { release.await(5, TimeUnit.SECONDS); } catch (InterruptedException e) { Thread.currentThread().interrupt(); }
        });
        assertTrue(started.await(1, TimeUnit.SECONDS));
        pool.execute(() -> { });
        try {
            worker.consume(1L, channel, 9);
            verify(channel).basicNack(9, false, false); verifyNoInteractions(tasks, transcoder, store);
        } finally { release.countDown(); }
    }
}
