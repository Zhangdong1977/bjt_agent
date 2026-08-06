import { Modal } from 'ant-design-vue'

/**
 * 提交检查成功后，提示用户预计耗时与稍后查看结果的入口。
 * 返回的 Promise 在用户点击「知道了」关闭弹窗后 resolve，调用方可据此决定后续跳转。
 */
export function showCheckDurationNotice(): Promise<void> {
  return new Promise((resolve) => {
    Modal.info({
      title: '检查已开始',
      content:
        '检查预计需要 20-30 分钟左右。为了节省您的时间，您可以先处理其他工作，30 分钟后进入【历史标书】菜单，通过历史检查记录查看检查结果。',
      okText: '知道了',
      onOk: () => resolve(),
    })
  })
}
